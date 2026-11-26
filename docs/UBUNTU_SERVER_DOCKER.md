**For a one-command installation, see [HUB_QUICKSTART.md](HUB_QUICKSTART.md).** The existing detailed manual below describes advanced setup. The Hub now provides centralized updates; do not use the old manual overwrite procedure for the hub deployment.

# Ubuntu Server / Docker — UHV Sentinel LAN Edition

UHV Sentinel's **headless Docker edition** runs independently of the Windows/Linux/macOS desktop GUI. It stores separate systems and SQLite history on Ubuntu Server and serves a **read-only instrument dashboard** on your LAN using local HTTPS. It does **not** provide remote HV, ion-gauge emission or TDK output controls. Keep the physical controllers' protective interlocks enabled.

## 1. Before removing the old Windows installation

Confirm with university IT that you may repurpose this PC and assign/keep its IP address. An IP previously set in Windows is **not** automatically yours for Ubuntu. Obtain the correct interface, IP/netmask, gateway, DNS, firewall policy and LAN client subnets. First boot Ubuntu Server from USB to test the MCT `0711:0230` USB-RS232 adapter; Linux often provides the `mct_u232` driver. Check actual serial functionality before relying on it.

## 2. Install Ubuntu Server + Docker Engine

Install Ubuntu Server and OpenSSH only if needed. Follow the [official Docker Engine Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/) for Docker Engine and the Compose plugin. Do **not** expose the Docker daemon over TCP or use `privileged: true`. Confirm:

```bash
docker --version
docker compose version
ip -4 address
ls -l /dev/serial/by-id/ /dev/ttyUSB* 2>/dev/null
lsusb
```

A fixed host IP may be configured using Ubuntu Netplan, with university-approved settings. Configure the university firewall/network ACL to allow only authorized laboratory/VPN computers to reach the selected server interface/port; do not set up any public DNS, Internet port forwarding or cloud tunnel. Merely having a fixed IP, including a routable university IP, is **not** evidence of private accessibility.

## 3. Download and initialize the server

Use Git or download/extract `uhv-sentinel-server-release.zip` from [GitHub Releases](https://github.com/maxlpld/UHV-sentinel/releases). In the extracted repository root:

```bash
python3 scripts/server_setup.py
```

The script creates `server-data/secrets/admin_password`, a cryptographically random session key, persistent recording folders and `.env.server`. It **does not** change your networking or touch an existing installation. Save the generated administrator password securely: the script prints it only on first initialization. `server-data/` and `.env.server` are ignored by Git.

Initial settings use `127.0.0.1`, so only the Ubuntu Server itself can reach the dashboard. Start locally:

```bash
docker compose --env-file .env.server -f compose.server.yml up -d --build
docker compose --env-file .env.server -f compose.server.yml ps
```

Check `https://127.0.0.1:8443` from a browser on the server or using a local SSH forwarding session. The Caddy proxy creates an **internal local CA certificate**. Do not bypass certificate validation on lab systems; see TLS trust instructions below.

## 4. Allow other computers on the *local* network

Once IT confirms the LAN-only access rules, edit `.env.server` and replace **both** values with the server's authorized LAN IPv4 address:

```dotenv
UHV_BIND_IP=192.168.1.50
UHV_SERVER_NAME=192.168.1.50
```

`192.168.1.50` is an **example**, not your computer's actual address. For a university-routable IP such as `131.152.x.x`, insist on upstream access-control rules and a Docker-aware host firewall restriction before starting; otherwise that endpoint may be reachable from elsewhere. Docker-published ports can bypass ordinary UFW `INPUT` rules and can sometimes be reached through routed interfaces; follow the [Docker firewall documentation](https://docs.docker.com/engine/network/packet-filtering-firewalls/).

Restart:

```bash
docker compose --env-file .env.server -f compose.server.yml up -d
```

The only exposed port is **TCP 8443 on `UHV_BIND_IP`**. There is no public HTTP port, no Internet endpoint, no telemetry and no CDN. The application process listens on port 8765 **inside Docker only**. Clients use:

```text
https://192.168.1.50:8443/
https://192.168.1.50:8443/s/downstairs-a
https://192.168.1.50:8443/s/downstairs-b
```

## 5. Browser HTTPS certificate trust

Caddy uses `tls internal`; the root CA is local, not signed by a public CA. Extract the public root certificate:

```bash
docker compose --env-file .env.server -f compose.server.yml cp gateway:/data/caddy/pki/authorities/local/root.crt ./uhv-local-root.crt
```

Ask university IT to approve and distribute that certificate to authorized Windows clients' **Trusted Root Certification Authorities**. Never share Caddy's private CA key. If the server's IP is changed, update both settings and restart; the new IP needs a matching certificate. Do not ignore HTTPS certificate warnings as a permanent workaround.

## 6. Attach the serial instruments safely

Connect the instruments **physically to Ubuntu Server**, not Windows. Identify devices:

```bash
lsusb
ls -l /dev/serial/by-id/
dmesg | grep -Ei 'ttyUSB|ttyACM|mct_u232'
```

Add a `devices` section (or uncomment examples) under the `monitor:` service in `compose.server.yml` for each **real, verified** serial port:

```yaml
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
      - /dev/ttyUSB1:/dev/ttyUSB1
```

Never use `privileged: true` or mount all of `/dev` merely for convenience. Mapping names and order may change across USB reconnects; stable `/dev/serial/by-id/` references can be used on the *host side* when present, mapped to fixed container `/dev/ttyUSB0` names. The `.env.server` contains your host's dialout group GID so the non-root application user can access those mapped ports. Restart the stack when device mappings change.

**Important:** EpiMax PVCi/PVCiDuo RJ45 often carries RS232/RS485 rather than real Ethernet. An ASIX USB-to-Ethernet network adapter is not a serial adapter. The EpiMax network modes work only if the controller really supports Modbus TCP or an appropriate serial-to-TCP gateway exists. Test your actual model first.

## 7. Create multiple independent systems

1. Open the HTTPS dashboard, log in with the administrator password and click **+ New**.
2. Give the system a name and unique URL name (e.g. `downstairs-a`). Repeat for `downstairs-b`.
3. Open a system and click **+ Instrument**. Select Pfeiffer, Agilent, EpiMax, TDK, IMPAC or NGC3; set the mapped Linux serial port or verified TCP endpoint and baud/address. Use different physical ports for different systems.
4. Click **Export profile** to save the system settings/graph layout as JSON. Use **Import profile** to restore settings on this or another server. Profiles intentionally do *not* restore output states or enable emission.
5. Change graph columns and panel ordering in the browser. Measurement histories are always separate between systems.

The links above are **paths**, not separate Docker containers or separate exposed network ports. Adding more systems needs no extra port forwarding, firewall rules or Docker services.

## 8. Logs, updates, backups and uninstall

```bash
# Inspect health / errors
docker compose --env-file .env.server -f compose.server.yml ps
docker compose --env-file .env.server -f compose.server.yml logs -f monitor

# Pull the latest source revision (if cloned), then rebuild and restart
# git pull --ff-only
docker compose --env-file .env.server -f compose.server.yml up -d --build

# Stop; retain recorded data and settings
docker compose --env-file .env.server -f compose.server.yml down

# Backup persistent files while the service is stopped
cp -a server-data server-data-backup
```

The database is `server-data/records/uhv-server.sqlite3`; Caddy's internal CA is under `server-data/caddy-data`, and administrator credentials are under `server-data/secrets`. Retain all of `server-data/` in a secure offline backup. Never publish it or upload it to GitHub. To uninstall, run `docker compose ... down` and remove the extracted application folder; **keep `server-data/`** if you want the measurements, profiles and local CA. Only permanently erase it after verifying a backup and deliberately choosing to purge.

**Updating from a GitHub ZIP:** Extract the newer `uhv-sentinel-server-release.zip` over application files while preserving your existing `server-data/` and `.env.server`, then run `docker compose ... up -d --build`. The desktop **Options → Update** button does not update the Docker server; Docker updates are administrator-driven.

History is retained for 365 days by default (`UHV_RETENTION_DAYS`), and can be exported/archived for longer-term storage. This server is **not a certified safety or interlock system**. Pressure values are advisory and can become stale due to communication failures. The browser shows stale/disconnected states rather than keeping a last value marked live.

## Troubleshooting

- **No serial readings:** check `ls -l /dev/ttyUSB*`, `lsusb`, device mapping, dialout GID, correct baud rate and cable pinout. COM4 is a *Windows* port name and is not valid inside Docker.
- **No remote browser access:** check `.env.server`, Docker `ps`, the approved host IP, firewall and university VLAN rules. Do **not** expose 8443 to the public Internet or add port forwarding.
- **Browser certificate warning:** trust the Caddy internal root CA only if approved by IT and validate that the displayed hostname/IP matches the configured host.
- **Device present but data stale:** check `docker compose ... logs -f monitor`, verify controller status and serial replies; the supervisor reconnects automatically after failures without issuing HV/output-changing commands.



### Hub Settings → Ubuntu Server & Docker maintenance (v0.0.7)

The administrator can **Check for system updates**, **Install updates**, inspect a stage-based progress bar and live logs, and (after a successful install) **Restart Ubuntu Server now**. All RT-A/RT-B systems share one host; **installing packages, restarting Docker or rebooting temporarily pauses monitoring and recording**. Schedule updates outside of active experiments.

The check refreshes APT package lists and lists upgrade candidates. The install operation runs `apt-get update`, simulates `apt-get full-upgrade` and refuses to proceed if it would remove any packages, then runs a non-interactive `apt-get full-upgrade` preserving existing local config, refreshes the Caddy and Python Docker images, and restores the services with Docker Compose. A confirmed reboot is a separate action; **no automatic reboot occurs**. The progress bar shows completed phases, not the percentage of individual APT packages. Docker image updates are checked during installation, not counted in the APT upgrade count.

For security, the web container has **no Docker socket, sudo, or host shell access**. Authenticated, CSRF-protected requests produce fixed-action records processed by root-owned systemd units on the Ubuntu host. Logs and status are streamed back to the Hub from bounded records; the browser cannot submit arbitrary commands. The dashboard stays private to your configured interface; **outbound access** to Ubuntu repositories and Docker Hub is required to download updates. If your network restricts outbound access, contact university IT.

A successful reboot returns the dashboard when Docker auto-starts, provided the host networking, storage, Docker service and local firewall recover correctly. Do not rely on remote reboot as the only administrative access; keep SSH or local console access available.

For an existing managed Ubuntu installation, install v0.0.7 using:

```bash
curl -fsSL https://raw.githubusercontent.com/maxlpld/UHV-sentinel/v0.0.7/INSTALL_UBUNTU_SERVER.sh -o /tmp/uhv-install.sh
sudo bash /tmp/uhv-install.sh
```

The installer registers `uhv-sentinel-maintenance.{path,service}` and `uhv-sentinel-reboot.{path,service}`, without resetting profiles, local access settings, or database history.
