#!/usr/bin/env python3
"""Build deterministic GitHub Release ZIP plus SHA256SUMS.txt from source."""
from __future__ import annotations
import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from installer import APP_FILES, APP_DIRS
from version import __version__


def build(output: Path | None = None) -> tuple[Path, Path]:
    folder = output or ROOT / 'dist'
    folder.mkdir(parents=True, exist_ok=True)
    zip_file = folder / 'uhv-sentinel-release.zip'
    paths = [ROOT / item for item in APP_FILES]
    for directory in APP_DIRS:
        if (ROOT / directory).is_dir():
            paths.extend(p for p in (ROOT / directory).rglob('*')
                         if p.is_file() and not any(part.startswith('.') or part == "__pycache__"
                                                        for part in p.relative_to(ROOT).parts)
                         and p.suffix not in (".pyc", ".pyo"))
    if any(not path.is_file() for path in paths):
        raise RuntimeError('Release files missing from source repository')
    with zipfile.ZipFile(zip_file, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(paths):
            name = path.relative_to(ROOT).as_posix()
            # Constant timestamp for reproducibility; use reproducible relative paths.
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    digest = hashlib.sha256(zip_file.read_bytes()).hexdigest()
    checksums = folder / 'SHA256SUMS.txt'
    # The Docker build needs only the headless app, instrument modules and documentation.
    server_zip = folder / 'uhv-sentinel-server-release.zip'
    server_files = [
        'Dockerfile.server', 'compose.server.yml', '.dockerignore', 'INSTALL_UBUNTU_SERVER.sh',
        'deploy/Caddyfile', 'scripts/server_setup.py', 'scripts/host_tasks.py', 'scripts/system_maintenance.py', 'scripts/map_serial.py',
        'docs/UBUNTU_SERVER_DOCKER.md', 'README.md', 'CHANGELOG.md',
        'version.py', 'vacuum_extra_devices.py', 'epimax_network.py',
        'tdk_lambda.py', 'impac_pyrometer.py',
    ]
    server_paths = [ROOT / name for name in server_files]
    server_paths += sorted(path for path in (ROOT / 'deploy' / 'systemd').rglob('*') if path.is_file())
    server_paths += sorted(path for path in (ROOT / 'server').rglob('*')
                           if path.is_file() and '__pycache__' not in path.parts
                           and path.suffix != '.pyc')
    with zipfile.ZipFile(server_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(server_paths):
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
    server_digest = hashlib.sha256(server_zip.read_bytes()).hexdigest()
    checksums.write_text(f'{digest}  {zip_file.name}\n{server_digest}  {server_zip.name}\n', encoding='ascii')
    print(f'UHV Sentinel v{__version__}')
    print(zip_file)
    print(server_zip)
    print(checksums)
    print(f'SHA-256: {digest}')
    return zip_file, checksums


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', type=Path)
    args = parser.parse_args()
    build(args.out_dir)
