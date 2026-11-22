#!/usr/bin/env python3
"""Optional one-time, non-destructive migration from original NGC3 user files."""
from __future__ import annotations
import shutil
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_paths import config_path, data_dir


def main() -> None:
    old_config = Path.home() / '.ngc3_gui_config.json'
    old_data = Path.home() / 'NGC3_pressure_monitor'
    target = data_dir()
    target.mkdir(parents=True, exist_ok=True)
    sources = []
    if old_config.is_file():
        sources.append((old_config, config_path()))
    if old_data.is_dir():
        sources.extend((path, target / path.name) for path in sorted(old_data.glob('*.sqlite3')))
    if not sources:
        print('No legacy config or SQLite recording files found. Nothing to migrate.')
    for original, destination in sources:
        if destination.exists():
            print(f'SKIP existing file: {destination}')
        else:
            shutil.copy2(original, destination)
            print(f'COPIED {original} -> {destination}')
    print('Original NGC3 data is unchanged. Review new app device settings before live use.')


if __name__ == '__main__':
    main()
