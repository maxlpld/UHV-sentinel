#!/usr/bin/env python3
"""UHV Sentinel Ubuntu host maintenance worker; ONLY systemd executes this as root.

The browser process can write only a small, fixed-kind request. It cannot select
commands, packages, environment variables, paths, or reboot timings. An outbound
network connection is needed for APT and Docker image upgrades; the monitoring
HTTP endpoint remains bound to the configured private address.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import os
import select
import signal
import subprocess
import time
from collections import deque
from pathlib import Path
import sys

# systemd invokes this by absolute path; ensure package imports work without
# relying on the current working directory.
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
from scripts import host_tasks

ROOT = host_tasks.ROOT
DATA = host_tasks.DATA
LOG_DIR = Path('/var/log/uhv-sentinel')
LOCK = Path('/run/lock/uhv-sentinel-ops.lock')
STATUS_NAME = 'maintenance-status.json'
REQUEST_NAME = 'maintenance-request.json'
REBOOT_NAME = 'maintenance-reboot.json'
VALID = {'check', 'install'}
MAX_LOG_LINES = 140
MAX_LOG_CHARS = 320
REQUEST_TTL = 3600


def _boot_id():
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return None


def _load(path: Path) -> dict:
    # Do not follow symlinks from the unprivileged Docker-shared data directory.
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    if os.fstat(fd).st_size > 262144:
        os.close(fd)
        raise ValueError('Maintenance metadata too large')
    try:
        with os.fdopen(fd, 'r', encoding='utf-8') as stream:
            value = json.load(stream)
    finally:
        pass
    if not isinstance(value, dict):
        raise ValueError('Expected maintenance JSON object')
    return value


def _checked_request(path: Path, kinds: set[str]):
    obj = _load(path)
    if set(obj) != {'kind', 'requested_at'}:
        raise ValueError('Unexpected request fields')
    if obj.get('kind') not in kinds:
        raise ValueError('Invalid maintenance operation')
    age = time.time() - float(obj['requested_at'])
    if not 0 <= age <= REQUEST_TTL:
        raise ValueError('Maintenance request has expired')
    return obj


class Maintenance:
    def __init__(self):
        self.state = {'state': 'idle', 'progress': 0, 'stage': '', 'updated_at': time.time(),
                      'logs': [], 'can_reboot': False}
        self.tail = deque(maxlen=MAX_LOG_LINES)
        self.last_write = 0.0
        self.file = None

    def start_log(self):
        # This is a root-owned directory; Docker cannot replace the log path.
        LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.file = (LOG_DIR / 'system-maintenance.log').open('a', encoding='utf-8')
        self.line('--- Maintenance request started ---')

    def line(self, text: str):
        text = str(text).rstrip()[:MAX_LOG_CHARS]
        if not text: return
        line = time.strftime('%Y-%m-%d %H:%M:%S') + '  ' + text
        self.tail.append(line)
        if self.file:
            self.file.write(line + '\n')
            self.file.flush()
        self.publish(force=False)

    def publish(self, force=True, **fields):
        self.state.update(fields)
        if not force and time.monotonic() - self.last_write < 1:
            return
        self.last_write = time.monotonic()
        host_tasks._write(STATUS_NAME, {**self.state, 'logs': list(self.tail),
                                        'updated_at': time.time()})

    def step(self, percent: int, message: str, state='installing'):
        self.state.update(state=state, progress=percent, stage=message)
        self.line(message)
        self.publish(force=True)

    def run(self, *args: str, timeout=5400, env=None) -> str:
        """Stream fixed-argv subprocess output without shell or unbounded hangs."""
        self.line('$ ' + ' '.join(args))
        values = {**os.environ, 'DEBIAN_FRONTEND': 'noninteractive',
                  'NEEDRESTART_MODE': 'a', 'LC_ALL': 'C', **(env or {})}
        proc = subprocess.Popen(args, cwd=str(ROOT), env=values,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True, bufsize=0)
        assert proc.stdout is not None
        capture = deque(maxlen=600)
        buffer = b''
        deadline = time.monotonic() + timeout
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'{args[0]} exceeded its {timeout}-second timeout')
                readable, _, _ = select.select([proc.stdout], [], [], 1)
                if not readable:
                    self.publish(force=False)  # refresh heartbeat even with no output
                    continue
                chunk = os.read(proc.stdout.fileno(), 8192)
                if not chunk:
                    break
                buffer += chunk
                # Treat carriage returns as progress events, not escaped UI output.
                buffer = buffer.replace(b'\r', b'\n')
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    cleaned = line.decode('utf-8', 'replace').strip()
                    if cleaned:
                        capture.append(cleaned)
                        self.line(cleaned)
                # Cap pending non-newline output from noisy programs.
                if len(buffer) > 16384:
                    self.line(buffer[:16000].decode('utf-8', 'replace'))
                    buffer = b''
            if buffer.strip():
                cleaned = buffer.decode('utf-8', 'replace').strip()
                capture.append(cleaned)
                self.line(cleaned)
            ret = proc.wait(timeout=20)
            if ret != 0:
                raise RuntimeError(f'{args[0]} exited with code {ret}; see maintenance logs')
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except OSError: pass
            proc.stdout.close()
        return '\n'.join(capture)


def _apt_packages(lines: str) -> list[str]:
    """Read `apt list --upgradable`, ignoring headers, without untrusted execution."""
    result = []
    for row in lines.splitlines():
        if '/' not in row or row.startswith('Listing...'):
            continue
        name = row.split('/', 1)[0].strip()
        if re.fullmatch(r'[a-z0-9][a-z0-9+._:-]*', name):
            result.append(name)
    return result


def _upgrade_plan(simulated: str):
    removals = []
    for line in simulated.splitlines():
        if line.startswith('Remv '):
            part = line.split()
            if len(part) > 1: removals.append(part[1])
    return removals


def _check(app: Maintenance):
    app.step(5, 'Refreshing Ubuntu package lists (apt-get update)', 'checking')
    app.run('apt-get', '-o', 'DPkg::Lock::Timeout=300', 'update', timeout=1500)
    app.step(50, 'Inspecting installed package versions', 'checking')
    raw = app.run('apt', 'list', '--upgradable', timeout=120)
    candidates = _apt_packages(raw)
    plan = app.run('apt-get', '-s', 'full-upgrade', timeout=180)
    removals = _upgrade_plan(plan)
    app.step(85, 'Checking Docker runtime', 'checking')
    docker = app.run('docker', '--version', timeout=20).strip().splitlines()[-1]
    compose = app.run('docker', 'compose', 'version', timeout=20).strip().splitlines()[-1]
    reboot = Path('/var/run/reboot-required').exists()
    status = 'up-to-date' if not candidates else 'ready'
    app.step(100, 'Check complete', status)
    app.publish(checked_at=time.time(), packages_count=len(candidates),
                packages=candidates[:100], removals=removals,
                docker_version=docker[:120], compose_version=compose[:120],
                image_update_note='Container image update availability is verified during installation.',
                reboot_required=reboot,
                can_install=not removals,
                message=(f'{len(candidates)} Ubuntu packages can be upgraded.' if candidates else
                         'No Ubuntu package upgrades found; Docker image refresh still available.'),
                can_reboot=False)
    if removals:
        app.publish(message='Full-upgrade would REMOVE packages: ' + ', '.join(removals[:12]) +
                              '. Automatic installation is blocked; review from SSH.',
                    can_install=False)
    return app.state


def _install(app: Maintenance, previous: dict):
    if previous.get('state') not in ('ready', 'up-to-date'):
        raise ValueError('Check for updates before installing')
    if time.time() - float(previous.get('checked_at', 0)) > 3600:
        raise ValueError('Package-check results are more than one hour old; check again')
    if previous.get('removals') or not previous.get('can_install'):
        raise ValueError('Automatic upgrade blocked: package removal requires administrator review')
    app.state.update({k:previous.get(k) for k in
                      ('checked_at', 'packages_count', 'docker_version', 'compose_version',
                       'packages', 'removals')})
    app.step(8, 'Refreshing Ubuntu package indexes')
    app.run('apt-get', '-o', 'DPkg::Lock::Timeout=300', 'update', timeout=1500)
    # Re-simulate immediately before applying, as repository contents may have changed.
    app.step(25, 'Validating package upgrade plan')
    simulated = app.run('apt-get', '-s', 'full-upgrade', timeout=180)
    removals = _upgrade_plan(simulated)
    if removals:
        raise RuntimeError('Upgrade cancelled: would remove ' + ', '.join(removals[:12]))
    app.step(35, 'Installing Ubuntu and Docker package updates (apt-get full-upgrade)')
    app.run('apt-get', '-o', 'DPkg::Lock::Timeout=300',
            '-o', 'Dpkg::Options::=--force-confdef',
            '-o', 'Dpkg::Options::=--force-confold',
            '-y', 'full-upgrade', timeout=7200)
    app.step(75, 'Downloading updated Docker images (Caddy and Python base)')
    compose = host_tasks._compose()
    app.run(*compose, 'pull', 'gateway', timeout=1800)
    app.run(*compose, 'build', '--pull', 'monitor', timeout=2400)
    app.step(90, 'Recreating Docker containers and restoring laboratory dashboard')
    app.run(*compose, 'up', '-d', '--no-build', timeout=900)
    app.step(95, 'Checking container state')
    containers = app.run(*compose, 'ps', '-q', 'monitor', timeout=120).strip()
    if not containers:
        raise RuntimeError('Monitor container is not running; use sudo uhv-sentinel status and logs')
    app.step(100, 'Ubuntu and Docker maintenance complete', 'completed')
    app.publish(completed_at=time.time(), reboot_required=Path('/var/run/reboot-required').exists(),
                can_reboot=True, message='Maintenance finished. You may restart this Ubuntu Server now; monitoring will temporarily stop.')


def process():
    req = DATA / REQUEST_NAME
    if not req.exists(): return
    app = Maintenance()
    # Serializes GitHub app updates and OS updates; never replace code during APT.
    with LOCK.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not req.exists():return
        try:
            request = _checked_request(req, VALID)
            # Never read a user-supplied command or pass request fields to a subprocess.
            previous = {}
            current = DATA / STATUS_NAME
            if current.exists():
                try: previous = _load(current)
                except (OSError, ValueError, TypeError): pass
            app.start_log()
            app.state.update(requested_kind=request['kind'])
            if request['kind'] == 'check': _check(app)
            else: _install(app, previous)
        except Exception as exc:
            app.line('FAILED: ' + str(exc)[:MAX_LOG_CHARS])
            app.publish(state='error', stage='Maintenance failed', error=str(exc)[:250],
                        can_install=False, can_reboot=False)
        finally:
            # Request is removed *after* the operation so the dashboard knows it is busy.
            req.unlink(missing_ok=True)
            if app.file: app.file.close()


def reboot():
    req = DATA / REBOOT_NAME
    if not req.exists():return
    with LOCK.open('a+') as lock:
        # Avoid reboot while full-upgrade / Docker installation is running.
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        try:
            _checked_request(req, {'reboot'})
            status = _load(DATA / STATUS_NAME)
            if status.get('state') != 'completed' or not status.get('can_reboot'):
                raise ValueError('A successful maintenance run is required before a web reboot')
            # Clear request before triggering system reboot to avoid a reboot loop.
            req.unlink(missing_ok=True)
            host_tasks._write(STATUS_NAME, {**status, 'state':'rebooting', 'stage':'System reboot requested',
                                           'can_reboot':False,'reboot_requested_at':time.time(),
                                           'boot_id_before':_boot_id(), 'updated_at':time.time()})
            subprocess.run(['systemctl', 'reboot'], check=True, timeout=20)
        except Exception as exc:
            req.unlink(missing_ok=True)
            host_tasks._write(STATUS_NAME, {'state':'error', 'stage':'Reboot request failed',
                                          'error':str(exc)[:250], 'updated_at':time.time()})


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Run host maintenance as root through systemd, not inside Docker')
    parser = argparse.ArgumentParser()
    parser.add_argument('task', choices=('process', 'reboot'))
    opts = parser.parse_args()
    process() if opts.task == 'process' else reboot()
