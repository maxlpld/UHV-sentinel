# UHV Sentinel Hub — Ubuntu Server quickstart

> This software is **not a vacuum interlock or safety system**. Instrument integration is read-only. The Docker service does not provide remote power-supply output/HV/emission control.

## Install on Ubuntu Server 24.04 / 26.04

1. Make sure university IT has approved use of this computer and the IP address. Never expose the UHV Sentinel web port on the public Internet.
2. On the Ubuntu Server, open a terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/maxlpld/UHV-sentinel/v0.0.7/INSTALL_UBUNTU_SERVER.sh -o /tmp/uhv-install.sh
sudo bash /tmp/uhv-install.sh
```

You can inspect `/tmp/uhv-install.sh` before running it as root. The installer sets up Docker Engine and Compose from Ubuntu packages if needed, installs UHV Sentinel at `/opt/uhv-sentinel`, creates a dedicated unprivileged service user, local HTTPS gateway, admin credentials, persistent SQLite data, host-status agent and update service, and builds the containers. Existing settings/history are preserved.

The installer defaults to **localhost-only** (`127.0.0.1:8443`) and does not edit the network IP, change your campus firewall or assign an IP to Ubuntu. To access the dashboard from another PC, after approval, edit both `UHV_BIND_IP` and `UHV_SERVER_NAME` in `/opt/uhv-sentinel/.env.server` to the IT-approved LAN address, then run:

```bash
sudo uhv-sentinel restart
```

Then open `https://<SERVER-IP>:8443/` on the authorized client. Ask IT to review the network ACL and deploy the internal HTTPS CA trust certificate; a university-routable IP is **not** automatically LAN-only. Docker port publishing may bypass UFW rules.

### The first sign-in

On the Ubuntu Server, show the generated password only to the administrator:

```bash
sudo cat /opt/uhv-sentinel/server-data/secrets/admin_password
```

After signing in, use **+ Add system** to create e.g. `RT-A` with slug `rt-a` and `RT-B` with slug `rt-b`. Each gets its own URL, profile, device configuration and database history:

- `https://<SERVER-IP>:8443/s/rt-a`
- `https://<SERVER-IP>:8443/s/rt-b`

Under each system, **+ Instrument** assigns real physical USB-serial adapters to the correct chamber; **P/A chamber cards** chooses which pressure channels are displayed on the hub. No placeholder values are treated as live readings. You can create further systems and add custom shortcut buttons on the hub homepage.

### USB serial devices

Connect RS232 adapters to Ubuntu, then run:

```bash
sudo uhv-sentinel devices
```

This enumerates currently connected USB serial adapters and updates an explicit Docker device mapping. Always verify the correct adapter, port, baud rate and cable; EpiMax RJ45 may be serial rather than Ethernet. No `privileged` mode or full `/dev` mount is used.

### Server health and software updates

The main homepage shows **host storage, RAM, CPU temperature when a compatible sensor is available, uptime, and software version**. These are collected on the Ubuntu host by a small systemd timer; no hardware readings are invented if a sensor is absent. The app checks GitHub for a new release, and **Home → Hub settings → Check for updates → Install latest update** queues an update for a restricted host systemd updater. Docker has **no Docker socket mount** or direct host command execution. Internet access is used **outbound only** to GitHub for updates, not to publish the dashboard.

### Useful maintenance commands

```bash
sudo uhv-sentinel status
sudo uhv-sentinel logs
sudo uhv-sentinel devices
sudo uhv-sentinel restart
sudo uhv-sentinel backup
```

To uninstall without deleting recordings:

```bash
cd /opt/uhv-sentinel
sudo docker compose --env-file .env.server -f compose.server.yml -f compose.devices.yml down
sudo systemctl disable --now uhv-sentinel-host.timer uhv-sentinel-update.path
```

The persistent folder `/opt/uhv-sentinel/server-data` contains recordings, credentials, settings and local TLS root certificate. Back it up before removing the program. Do not upload it to GitHub.

### Notes and troubleshooting

- The first boot must be connected to the network to download Ubuntu packages, source code, and the official container images. The **web app is local-only** unless you intentionally bind a LAN address.
- Local HTTPS uses Caddy's private CA. Ask university IT before distributing/trusting the local root certificate.
- Web updates restart the monitor container; readings will pause for the restart. Never rely on the dashboard for vacuum interlocks.
- Device mappings are fixed at container start; reconnecting an adapter may require running `sudo uhv-sentinel devices` again.
- Server temperature relies on the computer exposing supported `/sys/class/thermal` or `/sys/class/hwmon` measurements. A missing sensor displays `Unavailable`.
- Keeping a campus firewall configured is essential. Binding to one IP restricts the listener, **not necessarily the source network**; configure campus ACLs/Docker-aware firewall rules.

For advanced Docker settings and certificate handling, see [UBUNTU_SERVER_DOCKER.md](UBUNTU_SERVER_DOCKER.md).



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
