#!/usr/bin/env python3
"""Generate an explicit Docker serial mapping override; no privileged /dev mount."""
from __future__ import annotations
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'compose.devices.yml'

def collect():
    paths=[]
    # stable host symlink paths; Docker resolves symlink at container creation.
    by_id=Path('/dev/serial/by-id')
    if by_id.exists():
        paths=sorted((item for item in by_id.iterdir() if item.is_symlink()),key=lambda p:p.name)
    if not paths:
        paths=sorted(Path('/dev').glob('ttyUSB*'))+sorted(Path('/dev').glob('ttyACM*'))
    return paths

def main(update=False):
    if OUTPUT.exists() and not update:
        print('Existing serial mapping preserved:',OUTPUT);return
    devices=collect()
    lines=['# Auto-generated explicit device mapping. Edit on Ubuntu Server if needed.',
           'services:', '  monitor:']
    if devices:
        lines.append('    devices:')
        for i,path in enumerate(devices):
            target=f'/dev/ttyUSB{i}' if 'USB' in str(path.resolve()) else f'/dev/ttyACM{i}'
            lines.append(f'      - "{path}:{target}"')
        print('Mapped:',', '.join(str(p) for p in devices))
    else:
        lines[-1]='  monitor: {}'
        lines.append('    # Connect USB serial devices, then run: sudo uhv-sentinel devices')
        print('No attached USB serial adapters. Add mappings later with: sudo uhv-sentinel devices')
    OUTPUT.write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser();g=parser.add_mutually_exclusive_group(required=True)
    g.add_argument('--create',action='store_true');g.add_argument('--update',action='store_true')
    args=parser.parse_args();main(update=args.update)

