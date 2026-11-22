# Save and restore UHV Sentinel workspaces

Use **Profiles** in the toolbar or **Options → Profiles**.

- **Save current**: name the currently configured workspace and save it in the app data folder (`data/profiles/<profile-name>.json`).
- **Load selected**: stop the current monitors, reconnect devices described in the profile where serial ports are present, and restore saved settings.
- **Import...**: add a `.json` profile from another computer to the app's profiles folder.
- **Export...**: create a portable `.json` copy for sharing or backup.
- **Delete**: remove a selected profile without deleting pressure, temperature or TDK recordings.

## What is saved?

Saved profiles include the **instrument list and display names, serial port paths, baud rates, addressing, model protocol, graph ranges, panel order, column count (1–3), window size, alert/graph/theme preferences**, and applicable TDK hardware limit fields. The selected theme JSON is embedded so it can be imported on another computer. The custom theme is installed to the persistent `data/themes/` folder when the profile is loaded.

The position of the window on a specific screen is intentionally not imported, since monitors and desktop arrangements differ. Saved current configuration (outside profiles) can restore more UI state locally.

**Excluded for security and safety:** passwords, bearer tokens, previous NGC3 emission/automatic control state, and TDK output/armed state are never stored in a portable profile. The program does not automatically energize or modify the supply on profile load. SMTP passwords must be entered or provided through the environment again.

## Portable connection remapping

Port names differ between computers (`COM5` on Windows versus `/dev/ttyUSB0` on Linux). When a saved port does not currently appear among the detected serial ports, the profile loader offers manual port remapping. Choose a new port or leave that device offline without losing its configuration. Reimport/load the profile when the adapter becomes available. When an imported port is detected, UHV Sentinel attempts to reconnect automatically. Always confirm connector pinouts, serial settings, model and actual measurement values after switching computers.

## Where profiles live and how updates affect them

Profiles are **not** stored within the replaceable application source/installation files. They live alongside your existing data directory, in the **same UHV Sentinel application folder**, and survive normal reinstall/update/uninstall:

| Platform | Profiles directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\uhv-sentinel\data\profiles\` |
| Linux | `~/.local/share/uhv-sentinel/data/profiles/` (respects `XDG_DATA_HOME`) |
| macOS | `~/Library/Application Support/uhv-sentinel/data/profiles/` |

Performing an explicit **uninstall --purge-data** removes these profiles and all measurements, so export profiles and back up SQLite files first.
