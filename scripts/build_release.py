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
    checksums.write_text(f'{digest}  {zip_file.name}\n', encoding='ascii')
    print(f'UHV Sentinel v{__version__}')
    print(zip_file)
    print(checksums)
    print(f'SHA-256: {digest}')
    return zip_file, checksums


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', type=Path)
    args = parser.parse_args()
    build(args.out_dir)
