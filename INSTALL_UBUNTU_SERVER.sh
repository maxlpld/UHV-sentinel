#!/usr/bin/env bash
# One-command Ubuntu Server setup. Run as root using sudo; does not overwrite lab data.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
BASE=/opt/uhv-sentinel
REPO=https://github.com/maxlpld/UHV-sentinel.git
TAG=${UHV_VERSION:-v0.0.7}
if [[ $EUID -ne 0 ]]; then echo 'Run: sudo bash INSTALL_UBUNTU_SERVER.sh'; exit 1; fi
source /etc/os-release
if [[ ${ID:-} != ubuntu ]]; then echo 'Ubuntu Server required.' >&2; exit 1; fi
if [[ $TAG != v[0-9]*.[0-9]*.[0-9]* ]];then echo 'Invalid version tag' >&2;exit 1;fi
printf '\n== UHV Sentinel Hub / Ubuntu Server installer ==\n'
echo 'Installing Ubuntu-maintained packages docker.io, docker-compose-v2, git and python3.'
apt-get update -qq
apt-get install -y git python3 ca-certificates curl
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  # Use Ubuntu-maintained packages. Refuse to replace a pre-existing Docker CE setup.
  if dpkg-query -W -f='${Status}' docker-ce 2>/dev/null | grep -q 'install ok installed'; then
    echo 'Docker CE is installed but Docker Compose is missing; install docker-compose-plugin from Docker’s official repository.' >&2
    exit 1
  fi
  apt-get install -y docker.io docker-compose-v2
fi
systemctl enable --now docker
if ! docker compose version >/dev/null 2>&1; then echo 'Docker Compose v2 unavailable. See official Ubuntu Docker documentation.' >&2;exit 1;fi
if [[ ! -d $BASE/.git ]]; then
  if [[ -e $BASE ]];then echo "Existing $BASE is not a Git repository; stopping to protect data." >&2;exit 1;fi
  git clone --depth 1 --branch "$TAG" "$REPO" "$BASE"
else
  echo "Existing UHV Sentinel installation found in $BASE."
  ORIGIN=$(git -C "$BASE" remote get-url origin)
  if [[ "$ORIGIN" != "$REPO" && "$ORIGIN" != "git@github.com:maxlpld/UHV-sentinel.git" ]]; then
    echo "Unexpected Git origin: $ORIGIN. Refusing to update." >&2; exit 1
  fi
  if ! git -C "$BASE" diff --quiet || ! git -C "$BASE" diff --cached --quiet; then
    echo 'Local application source has modifications. Back it up before updating.' >&2;exit 1
  fi
  git -C "$BASE" fetch --depth 1 origin tag "$TAG"
  git -C "$BASE" checkout --force --detach "$(git -C "$BASE" rev-parse FETCH_HEAD)"
fi
cd "$BASE"
if ! id uhv-sentinel >/dev/null 2>&1;then useradd --system --create-home --home-dir /var/lib/uhv-sentinel --shell /usr/sbin/nologin uhv-sentinel;fi
install -d -o uhv-sentinel -g uhv-sentinel -m 0700 server-data/secrets server-data/records
install -d -m 0755 server-data/caddy-data server-data/caddy-config
if [[ ! -f .env.server ]];then
  UHV_RUNTIME_UID="$(id -u uhv-sentinel)" UHV_RUNTIME_GID="$(id -g uhv-sentinel)" python3 scripts/server_setup.py
  chown root:root .env.server
  chmod 600 .env.server
else
  echo 'Using existing .env.server, profile and measurement database.'
fi
# Ensure monitor reads its own secrets and recordings.
chown -R uhv-sentinel:uhv-sentinel server-data/records server-data/secrets
chmod 700 server-data/secrets server-data/records
# Stable container target paths; map only existing USB serial adapters.
if [[ ! -f compose.devices.yml ]];then
  python3 scripts/map_serial.py --create
fi
install -m 0644 deploy/systemd/uhv-sentinel-host.service /etc/systemd/system/
install -m 0644 deploy/systemd/uhv-sentinel-host.timer /etc/systemd/system/
install -m 0644 deploy/systemd/uhv-sentinel-update.service /etc/systemd/system/
install -m 0644 deploy/systemd/uhv-sentinel-update.path /etc/systemd/system/
install -m 0644 deploy/systemd/uhv-sentinel-maintenance.service /etc/systemd/system/
install -m 0644 deploy/systemd/uhv-sentinel-maintenance.path /etc/systemd/system/
install -m 0644 deploy/systemd/uhv-sentinel-reboot.service /etc/systemd/system/
install -m 0644 deploy/systemd/uhv-sentinel-reboot.path /etc/systemd/system/
cat >/usr/local/sbin/uhv-sentinel <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd /opt/uhv-sentinel
case "${1:-}" in
 status) docker compose --env-file .env.server -f compose.server.yml ${UHV_COMPOSE_EXTRA:-} ps ;;
 logs) docker compose --env-file .env.server -f compose.server.yml ${UHV_COMPOSE_EXTRA:-} logs -f monitor ;;
 devices) python3 scripts/map_serial.py --update; docker compose --env-file .env.server -f compose.server.yml -f compose.devices.yml up -d --force-recreate monitor ;;
 restart) docker compose --env-file .env.server -f compose.server.yml -f compose.devices.yml up -d --build ;;
 backup) tar -czf "/root/uhv-sentinel-data-$(date +%Y%m%d-%H%M%S).tar.gz" server-data .env.server compose.devices.yml; echo 'Backup stored in /root' ;;
 update) python3 scripts/host_tasks.py update ;;
 maintenance) systemctl status uhv-sentinel-maintenance.service --no-pager ;;
 *) echo 'Usage: sudo uhv-sentinel {status|logs|devices|restart|backup|update|maintenance}'; exit 2 ;;
esac
EOF
chmod 0755 /usr/local/sbin/uhv-sentinel
systemctl daemon-reload
systemctl enable --now uhv-sentinel-host.timer uhv-sentinel-update.path uhv-sentinel-maintenance.path uhv-sentinel-reboot.path
systemctl start uhv-sentinel-host.service
# Protect credentials in logs, do not print password a second time.
docker compose --env-file .env.server -f compose.server.yml -f compose.devices.yml up -d --build
LAN_IP=$(awk -F= '/^UHV_BIND_IP=/ {print $2}' .env.server | head -1)
printf '\nINSTALLATION COMPLETE\nHub: https://%s:8443/\n' "${LAN_IP:-127.0.0.1}"
echo 'Initial binding is localhost only. Edit /opt/uhv-sentinel/.env.server with your IT-approved LAN IP, then run sudo uhv-sentinel restart.'
echo 'After approval: configure credentials, add RT-A and RT-B, and map serial adapters with sudo uhv-sentinel devices.'
echo 'ADMIN PASSWORD: read from /opt/uhv-sentinel/server-data/secrets/admin_password (root access required).'
