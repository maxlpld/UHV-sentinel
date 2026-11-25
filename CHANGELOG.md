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
