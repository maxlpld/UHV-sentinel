"""Authenticated hub summaries and host update status; never controls instruments."""
from __future__ import annotations
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from version import __version__

REPO = 'maxlpld/UHV-sentinel'
VERSION = re.compile(r'^v(\d+)\.(\d+)\.(\d+)$')
_lock = threading.Lock()
_cache: dict = {'at': 0, 'result': None}


def version_tuple(value: str):
    match = VERSION.fullmatch(value)
    return tuple(map(int, match.groups())) if match else None


def get_release_status(data_dir: Path, *, fetch: bool = True) -> dict:
    """Check read-only GitHub metadata; never execute an update in Docker."""
    result = {'installed': 'v' + __version__, 'latest': None, 'available': False,
              'release_url': f'https://github.com/{REPO}/releases/latest'}
    if fetch:
        with _lock:
            if time.time() - _cache['at'] >= 600 or _cache['result'] is None:
                try:
                    request = urllib.request.Request(
                        f'https://api.github.com/repos/{REPO}/releases/latest',
                        headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'UHV-Sentinel-Hub'})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        obj = json.loads(response.read(65536).decode('utf-8'))
                    tag = str(obj.get('tag_name', ''))
                    if version_tuple(tag) is None:
                        raise ValueError('Unrecognized release tag')
                    _cache['result'] = {'latest': tag, 'error': None}
                except Exception as exc:
                    _cache['result'] = {'latest': None,
                                        'error': 'GitHub could not be reached (' + type(exc).__name__ + ')'}
                _cache['at'] = time.time()
            result.update(_cache['result'])
        if result.get('latest'):
            result['available'] = version_tuple(result['latest']) > version_tuple('v' + __version__)
    for filename, key in (('update-status.json', 'status'),):
        path = Path(data_dir) / filename
        if path.exists():
            try:
                result[key] = json.loads(path.read_text('utf-8'))
            except (ValueError, OSError):
                result[key] = {'state': 'unknown', 'message': 'Unable to read updater status'}
    result['pending'] = (Path(data_dir) / 'update-request.json').exists()
    return result


def queue_update(data_dir: Path) -> None:
    data_dir = Path(data_dir)
    if (data_dir / 'update-request.json').exists():
        raise ValueError('An update request is already pending')
    # Do not accept a command, URL, tag or script from a browser payload.
    request = {'kind': 'install-latest-release', 'requested_at': time.time()}
    path = data_dir / 'update-request.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(request, stream)


def host_telemetry(data_dir: Path) -> dict:
    path = Path(data_dir) / 'host-stats.json'
    if not path.exists():
        return {'available': False, 'message': 'Host agent not installed or not started'}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or time.time() - float(data.get('updated_at', 0)) > 90:
            return {'available': False, 'message': 'Host measurements are stale'}
        return {'available': True, **{key: data.get(key) for key in
                ('hostname', 'updated_at', 'uptime_seconds', 'cpu_temp_c', 'cpu_temp_source',
                 'disk_total_bytes', 'disk_free_bytes', 'memory_total_bytes',
                 'memory_available_bytes', 'load_1m')}}
    except (OSError, ValueError, TypeError):
        return {'available': False, 'message': 'Cannot parse host measurements'}


def summarize_systems(store, poller) -> list:
    """Summaries never invent P/A pressure; mapping must be configured explicitly."""
    rows = []
    for system in store.systems():
        slug = system['slug']; live = poller.snapshot(slug)
        mapped = store.get_chambers(slug)
        chambers = {}
        for key, label in (('p', 'P-Chamber'), ('a', 'A-Chamber')):
            selection = mapped.get(key) or {}
            entry = live.get(selection.get('device_id'), {})
            metric = entry.get('metrics', {}).get(selection.get('metric'))
            fresh = (entry.get('state') == 'ok' and bool(metric) and
                     time.time() - float(entry.get('last_success') or 0) < 180)
            chambers[key] = {'label': label, 'configured': bool(selection),
                             'fresh': fresh, 'value': metric['value'] if fresh else None,
                             'unit': metric['unit'] if fresh else None,
                             'last_success': entry.get('last_success')}
        rows.append({'slug': slug, 'name': system['name'], 'devices': len(live),
                     'healthy': sum(item.get('state') == 'ok' for item in live.values()),
                     'url': '/s/' + slug, 'chambers': chambers})
    return rows
