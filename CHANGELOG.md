## v0.0.4 — Ubuntu Server / Docker LAN edition (2026-11-25)

- Headless Docker service for existing drivers (read-only acquisition).
- Password-protected local HTTPS dashboard and independent lab-system URLs.
- Browser instrument assignment, private per-system history, profiles and layout.
- Explicit non-wildcard host-IP binding, no privileged Docker mode, and mapped USB serial ports.
- Standalone server release ZIP, setup/update/uninstall docs and automated tests.

## v0.0.3 — reliability fixes (2026-11-23)

- Read Agilent pressure before optional diagnostic windows; stagger optional requests.
- Retry pressure reads and attempt rate-limited serial recovery on prolonged loss of data.
- Show stale/partial pressure status and improve Options → Update guidance and manual download fallback.
- Document manual migration from old v0.2.0 numbering.

## v0.0.2 — experimental EpiMax network support

- Added optional read-only Modbus TCP and transparent RTU-over-TCP transports.
- Added an EpiMax IP address and TCP port selector.
- Preserved serial support and panel/profile restoration for TCP endpoints.
- Restored GitHub CI and automatic release packaging.
- Hardware compatibility must be checked against the exact EpiMax model.

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
