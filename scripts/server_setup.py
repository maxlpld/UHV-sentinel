#!/usr/bin/env python3
"""Prepare Ubuntu Server secrets and a localhost-only Docker deployment.

Does not install Docker, change the network, open a firewall, or overwrite secrets.
"""
from __future__ import annotations
import getpass
import grp
import os
from pathlib import Path
import secrets

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'server-data'
ENV=ROOT/'.env.server'

def main():
    if os.name!='posix':raise SystemExit('Run the server setup on Ubuntu Server')
    os.umask(0o077)
    for p in ('secrets','records','caddy-data','caddy-config'):
        (BASE/p).mkdir(parents=True,exist_ok=True)
    password=BASE/'secrets'/'admin_password'
    key=BASE/'secrets'/'session_key'
    if not password.exists():
        proposed=secrets.token_urlsafe(24)
        password.write_text(proposed,encoding='utf-8');password.chmod(0o600)
        print('\nADMIN PASSWORD (save securely, displayed only once):\n'+proposed+'\n')
    else:
        print('Existing administrator password preserved at server-data/secrets/admin_password')
    if not key.exists():
        key.write_bytes(secrets.token_bytes(48));key.chmod(0o600)
    try:gid=grp.getgrnam('dialout').gr_gid
    except KeyError:gid=20
    if not ENV.exists():
        ENV.write_text(f'''# Use localhost until network access has been approved by university IT.
UHV_BIND_IP=127.0.0.1
UHV_SERVER_NAME=127.0.0.1
UHV_UID={int(os.environ.get("UHV_RUNTIME_UID", os.getuid()))}
UHV_GID={int(os.environ.get("UHV_RUNTIME_GID", os.getgid()))}
UHV_SERIAL_GID={gid}
UHV_RETENTION_DAYS=365
''',encoding='utf-8')
        ENV.chmod(0o600)
        print('Created .env.server with loopback-only binding (127.0.0.1).')
    else:
        print('Preserved your existing .env.server configuration.')
    print('Run: docker compose --env-file .env.server -f compose.server.yml up -d --build')
    print('First local URL: https://127.0.0.1:8443/ (Caddy internal certificate).')

if __name__=='__main__':main()
