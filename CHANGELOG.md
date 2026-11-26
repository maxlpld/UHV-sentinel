## v0.0.7 — Ubuntu Server maintenance center

- Hub Settings: authenticated Ubuntu and Docker update check/install, stage progress, live logs, and a separately confirmed host reboot.
- Root-owned systemd maintenance/reboot workers with fixed command schemas and operation locking; Docker itself remains unprivileged.
- Non-interactive apt full-upgrade with plan safety check, Docker image refresh, restart and state retention.
- Status survives browser disconnects and reports host boot-ID change after a reboot.
- Existing laboratory system configurations, profiles and recordings are unaffected by the change.

## v0.0.6 — Hub stabilization (2026-11-25)

- Close SQLite connections explicitly across Windows and Linux, avoiding locked databases.
- Include P-Chamber / A-Chamber mappings in exported profiles, including after ID remapping on import.
- Preserve mappings while a gauge is temporarily offline in the browser.
- Ubuntu installer now pins the v0.0.6 tagged source release.

## v0.0.5 — UHV Sentinel Hub (2026-11-25)

- Ubuntu Server hub homepage with hardware health (host metrics), software update status, custom shortcuts and dedicated systems.
- Each system is isolated and has its own path, serial device configuration, live graphs, history, profile and explicitly chosen P-Chamber / A-Chamber metrics.
- Moved server update control to Hub settings. A read-only Docker API queues updates for a restricted Ubuntu systemd host worker; Docker socket never enters the web container.
- One-command Ubuntu Server installation script, host stats and update services, serial adapter mapping helper, fast deployment quickstart and read-only instrument safeguards.
- Preserved the existing desktop edition and its drivers.

# Changelog

## v0.2.0 — TDK monitoring and portable profiles

- Introduced **TDK-Lambda Genesys (legacy)** and **TDK-Lambda Z+ SCPI** serial drivers.
- Added a voltage/current/power dashboard, continuous independent SQLite recording, time-range graphs, CSV export and read-only browser graphs.
- Added deliberately locked manual supply controls (explicit nameplate limits, session-only ARM, per-command confirmation; no automatic writes).
- Added **Profiles** GUI: save/load/import/export/delete and remapping of missing serial ports.
- Added configurable **1, 2 or 3 columns** and movable instrument-panel order.
- Preserved panel layout, connection metadata, settings, and portable embedded theme JSON, while excluding credentials and unsafe output states.
- Expanded offline protocol, persistence, profile and mock-GUI tests.

## v0.1.0 — Initial UHV Sentinel release

- AML NGC3, Pfeiffer DualGauge, Agilent 4UHV, EpiMax PVCi and IMPAC pyrometer integrations.
- Pressure and temperature graphs, history, alerts, local browser monitor.
- GitHub release, install, update and uninstall tooling.
