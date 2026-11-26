#!/usr/bin/env python3
"""Ubuntu host statistics and controlled update worker. Run under systemd, not HTTP."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'server-data' / 'records'
REPO = 'maxlpld/UHV-sentinel'
TAG = re.compile(r'^v\d+\.\d+\.\d+$')


def _write(name: str, data: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.uhv-', dir=DATA)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(data, file)
        os.replace(tmp, DATA / name)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def stats():
    """Fetch host system telemetry; unavailable sensors report null, not 0 °C."""
    values = {'updated_at': time.time(), 'hostname': socket.gethostname(),
              'uptime_seconds': None, 'cpu_temp_c': None, 'cpu_temp_source': None,
              'disk_total_bytes': None, 'disk_free_bytes': None,
              'memory_total_bytes': None, 'memory_available_bytes': None, 'load_1m': None,
              'boot_id': None}
    try:
        values['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError: pass
    try:
        values['uptime_seconds'] = float(Path('/proc/uptime').read_text().split()[0])
        values['load_1m'] = float(Path('/proc/loadavg').read_text().split()[0])
    except (OSError, IndexError, ValueError): pass
    try:
        mem = {}
        for row in Path('/proc/meminfo').read_text().splitlines():
            key, sep, rest = row.partition(':')
            if sep: mem[key] = int(rest.strip().split()[0]) * 1024
        values['memory_total_bytes'] = mem.get('MemTotal')
        values['memory_available_bytes'] = mem.get('MemAvailable')
    except (OSError, IndexError, ValueError): pass
    try:
        disk = os.statvfs(DATA)
        values['disk_total_bytes'] = disk.f_blocks * disk.f_frsize
        values['disk_free_bytes'] = disk.f_bavail * disk.f_frsize
    except OSError: pass
    candidates = []
    try:
        for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
            kind = (zone / 'type').read_text().strip()
            temp = float((zone / 'temp').read_text().strip()) / 1000
            if 0 < temp < 120 and any(x in kind.lower() for x in ('cpu', 'pkg', 'x86', 'k10', 'core', 'soc')):
                candidates.append((kind, temp))
        for hw in Path('/sys/class/hwmon').glob('hwmon*'):
            kind = (hw / 'name').read_text().strip()
            if any(x in kind.lower() for x in ('coretemp', 'k10temp', 'zenpower')):
                for sensor in hw.glob('temp*_input'):
                    temp = float(sensor.read_text().strip()) / 1000
                    if 0 < temp < 120: candidates.append((kind, temp))
    except (OSError, ValueError): pass
    if candidates:
        kind, temp = candidates[0]
        values['cpu_temp_c'] = round(temp, 1)
        values['cpu_temp_source'] = kind
    _write('host-stats.json', values)


def _run(*args, check=True):
    return subprocess.run(args, check=check, cwd=ROOT, capture_output=True, text=True, timeout=240)


def _latest():
    req = urllib.request.Request(f'https://api.github.com/repos/{REPO}/releases/latest',
                                 headers={'User-Agent': 'UHV-Sentinel-Host-Update'})
    with urllib.request.urlopen(req, timeout=10) as res:
        tag = json.loads(res.read(65536))['tag_name']
    if not TAG.fullmatch(tag): raise ValueError('Unexpected GitHub release tag')
    return tag


def _compose():
    args = ['docker','compose','--env-file','.env.server','-f','compose.server.yml']
    if (ROOT / 'compose.devices.yml').exists(): args += ['-f','compose.devices.yml']
    return args


def update():
    req = DATA / 'update-request.json'
    if not req.exists(): return
    with Path('/run/lock/uhv-sentinel-ops.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not req.exists(): return
        old = None
        try:
            requested = json.loads(req.read_text('utf-8'))
            if requested.get('kind') != 'install-latest-release':
                raise ValueError('Invalid update request')
            if time.time() - float(requested.get('requested_at', 0)) > 86400:
                raise ValueError('Update request has expired')
            tag = _latest()
            old = _run('git', '-C', str(ROOT), 'rev-parse', 'HEAD').stdout.strip()
            _run('git','-C',str(ROOT),'fetch','--depth','1','origin','tag',tag)
            target = _run('git','-C',str(ROOT),'rev-parse','FETCH_HEAD').stdout.strip()
            if old == target:
                _write('update-status.json', {'state':'up-to-date','version':tag,'updated_at':time.time()})
                return
            _write('update-status.json', {'state':'installing','version':tag,'updated_at':time.time()})
            _run('git','-C',str(ROOT),'checkout','--force','--detach',target)
            try:
                _run(*_compose(),'up','-d','--build',check=True)
                # Check the monitor service was brought up. Full serial hardware validation is on-site.
                _run(*_compose(),'ps','-q','monitor')
            except Exception:
                _run('git','-C',str(ROOT),'checkout','--force','--detach',old)
                _run(*_compose(),'up','-d','--build',check=False)
                raise
            _write('update-status.json', {'state':'success','version':tag,'updated_at':time.time()})
        except Exception as exc:
            _write('update-status.json', {'state':'error','message':str(exc)[:250], 'updated_at':time.time()})
        finally:
            req.unlink(missing_ok=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('task',choices=('stats','update'))
    args=p.parse_args()
    if args.task == 'stats': stats()
    else: update()
