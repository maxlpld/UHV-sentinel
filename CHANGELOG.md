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
