# UHV Sentinel — Laboratory Hub

**Ubuntu Server / Docker is the recommended deployment.** One always-on server monitors independently configured vacuum systems through a password-protected, local HTTPS dashboard. Start on the hub homepage, view hardware health/storage/uptime/CPU temperature (if detected), and open each system with a dedicated URL.

### Ubuntu Server 24.04 / 26.04 — copy these two commands

```bash
curl -fsSL https://raw.githubusercontent.com/maxlpld/UHV-sentinel/v0.0.5/INSTALL_UBUNTU_SERVER.sh -o /tmp/uhv-install.sh
sudo bash /tmp/uhv-install.sh
```

The script can be reviewed before execution; it installs Docker/Compose if needed, configures a local-only HTTPS dashboard, host monitoring, auto-restart, data storage, and the host-side GitHub updater. It will **never** assign the university IP for you or open the server to the public Internet. You must obtain university IT approval before allowing LAN access.

- **Hub:** `https://SERVER-IP:8443/`
- **RT-A:** `https://SERVER-IP:8443/s/rt-a`
- **RT-B:** `https://SERVER-IP:8443/s/rt-b`

The two system pages are created from the hub, and can have independent devices, pressure graphs, history, and profiles. Configure the hub's P-Chamber and A-Chamber tiles by explicitly selecting pressure channels in each system. Add as many other systems or local shortcut buttons as required.

The installer initially binds only `127.0.0.1`. After IT grants LAN-only access, set `UHV_BIND_IP` and `UHV_SERVER_NAME` in `/opt/uhv-sentinel/.env.server` to the approved IP, then run `sudo uhv-sentinel restart`. Get the initial administrator password with `sudo cat /opt/uhv-sentinel/server-data/secrets/admin_password`.

**Updates:** Hub → Main settings → Check for updates → Install latest update. The systemd host service performs the update, keeping the Docker socket out of the web app. The server performs read-only instrument measurements; it never activates TDK output, ion-gauge emission or pump HV remotely.

**[Full Ubuntu Server quickstart](docs/HUB_QUICKSTART.md)** · **[Advanced Docker and LAN configuration](docs/UBUNTU_SERVER_DOCKER.md)**

---

## Legacy desktop edition (Windows / Linux / macOS)


**UHV Sentinel** is a desktop application for laboratory vacuum and power-supply monitoring. Connect compatible serial instruments, graph pressure / temperature / electrical data, retain SQLite recordings, configure alerts, save portable workspaces and access a read-only browser dashboard on a private LAN.

**Current release: v0.2.0** · Python 3.10+ · Windows / Linux / macOS · [MIT license](LICENSE)

> **Lab safety:** UHV Sentinel is an informational monitor, not a certified safety interlock or pressure protection device. Check every new gauge's measured values and units against its front panel before relying on logs or alerts. Do not connect serial cables without checking each model's electrical interface.

## Supported hardware

| Instrument | Connection | Readings | Notes |
| --- | --- | --- | --- |
| AML NGC3 | RS232 | Ion/Pirani/active pressure, status | Original emission controls retained; disable remote controls in Options for monitoring-only behavior |
| Pfeiffer DualGauge **TPG 262** | RS232 | **Both** sensor pressures | One physical device and COM port, two traces |
| Agilent **4UHV 2x80W NEG** | J2 RS232 | Two ion-pump pressures, voltages, currents, HV/status/temperature/interlock and other supported diagnostics | New driver uses **read-only** Window Protocol commands |
| EpiMax **PVCi** | RJ45 **serial** → RS232 adapter | One ion gauge pressure, available Modbus diagnostics | **Not Ethernet** on documented PVCi; PVCiDuo second channel not validated |
| IMPAC **IN 140/5** | RS232 | Temperature and available diagnostics | Original pyrometer integration retained |
| TDK-Lambda **Genesys legacy GEN** | RS232 (connector model-specific) | Measured V, A, calculated W, V/A setpoints, output state | Manual confirmation-gated power output controls; monitor-only on connect |
| TDK-Lambda **Z+ SCPI** | RS232 or USB-serial (model-specific) | Measured V, A, calculated W, V/A setpoints, output state | Requires Z+ configured for SCPI command dialect; not every TDK model is supported |

**Important: an RJ45 connector does not prove Ethernet.** For the documented EpiMax PVCi/PVCiDuo variants, it is an RS232/RS485 serial connection. Do **not** connect the device to an Ethernet switch until the exact device model and port pinout are verified. Consult [the connection guide](docs/DEVICE_CONNECTION_GUIDE.md) for controller-specific warnings. For supplies, see the dedicated [TDK-Lambda connection and safety guide](docs/TDK_LAMBDA_GUIDE.md).

## Install for the first time

Download the source ZIP from your repository's GitHub **Code → Download ZIP**, or download the `uhv-sentinel-release.zip` asset under **Releases** and extract it to any folder. Run the installer from the **extracted** folder, not from inside the ZIP.

### Windows 10 / 11

1. Install [Python](https://www.python.org/downloads/) 3.10 or newer, with the Python Launcher (`py`).
2. Double-click `INSTALL_WINDOWS.bat` and follow the terminal output. It creates a private Python environment, installs `pyserial`, and makes an **UHV Sentinel** Desktop shortcut.
3. Start the application from the shortcut, select **Device**, serial **Port**, **Baud**, and click **Add**.

To run the installer manually from PowerShell:

```powershell
py -3 installer.py install
```

### Ubuntu / Debian Linux

Install the OS dependencies once:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-tk
```

In the extracted project folder:

```bash
bash INSTALL_LINUX_MAC.sh
```

You can launch **UHV Sentinel** from the application menu or the Desktop launcher (GNOME may ask you to select *Allow Launching*). A group membership change may be required to access `/dev/ttyUSB*` serial devices (commonly `dialout` on Ubuntu); log out/in after changes.

### macOS

Install Python 3.10+ with Tk support, then run from the extracted folder:

```bash
bash INSTALL_LINUX_MAC.sh
```

A Desktop `UHV Sentinel.command` launcher is created. If macOS blocks the terminal launcher, open it from Terminal or approve it under macOS security settings. Connect USB-serial adapters approved for your system.

**Installation location:** the installer works entirely within your user account; no administrator privileges are needed for the app itself. Python packages are installed in an app-specific virtual environment, not into system Python.

## Update from GitHub

In the running application:

**Options → Update → Check for updates → Install vX.Y.Z**

The app checks the repository's **latest stable GitHub Release**, compares versions, downloads the release archive, verifies the published **SHA-256** checksum, and asks for confirmation before installing. It then stops monitoring, closes serial ports, replaces only the program files, and restarts. **No automatic silent updates.**

For this button to work, the maintainer must first publish a **public GitHub Release** containing both:

- `uhv-sentinel-release.zip`
- `SHA256SUMS.txt`

The default release repository is `maxlpld/uhv-sentinel` in `release_config.json`. **If the repository does not exist yet or you choose another repository, update that setting and publish the first release before expecting update checks to work.** A private repository requires authentication that this version of the updater does not provide.

To update **without** GitHub network access, download a newer project ZIP, extract to a new temporary folder, and run the normal installer again. This safely replaces installed code while leaving recordings and settings intact. **Close the application before doing this.**

## Reinstall / repair

If a serial adapter driver or application dependency breaks:

1. Close UHV Sentinel.
2. Extract the desired version's ZIP to a temporary folder.
3. Run `INSTALL_WINDOWS.bat` or `bash INSTALL_LINUX_MAC.sh` again.

The installer reuses/repairs the app's private virtual environment and replaces its application files. **Your pressure history, temperature history, alerts and GUI configuration are kept separately** and remain available after reinstalling. The previous program version is retained as `previous_app` for recovery.

## Uninstall

Close the application. From a previously extracted UHV Sentinel ZIP run:

- **Windows:** double-click `UNINSTALL_WINDOWS.bat` (or `py -3 installer.py uninstall`).
- **Linux / macOS:** `bash UNINSTALL_LINUX_MAC.sh` (or `python3 installer.py uninstall`).

**Default uninstall preserves all recordings and settings.** For a permanent erase of *all* UHV Sentinel user data, and only if you explicitly want to delete it:

```bash
python3 installer.py uninstall --purge-data
```

On Windows replace `python3` with `py -3`. This last command is irreversible: back up your SQLite files first. It does not remove the original, older NGC3 project or its history.

## Saved data and preferences

| Operating system | Data/settings location | Installed program location |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\uhv-sentinel\data` | `%LOCALAPPDATA%\uhv-sentinel\installation` |
| Linux | `~/.local/share/uhv-sentinel/data` | `~/.local/share/uhv-sentinel/installation` |
| macOS | `~/Library/Application Support/uhv-sentinel/data` | `~/Library/Application Support/uhv-sentinel/installation` |

Linux respects `XDG_DATA_HOME` where set. The `data` directory contains `config.json`, weekly `pressure_history_YYYY-Www.sqlite3`, weekly **`tdk_measurements_YYYY-Www.sqlite3`**, pyrometer logs, the **`profiles/`** folder and temporary updater files. Ordinary updates, reinstalls and standard uninstall preserve this data directory. Monitoring continues to write new measurements to it while the app is running.

### Migrating recordings from the older NGC3 GUI

The older app used `~/.ngc3_gui_config.json` and `~/NGC3_pressure_monitor`. These are deliberately **not modified or imported automatically**. To copy these into UHV Sentinel once, before first launch, use:

```bash
python3 scripts/import_legacy.py
```

This makes copies only and never overwrites an existing UHV Sentinel config or database. On Windows use `py -3 scripts/import_legacy.py`. If you prefer a clean installation, skip this step.

## TDK-Lambda voltage and current recording

1. Select **TDK-Lambda power supply** from **Device**, choose the correct **Genesys legacy** or **Z+ SCPI** serial protocol, configure the port, baud and address, and click **Add**. Confirm the exact model and connector electrically before attaching any serial adapter.
2. The TDK panel shows **actual measured voltage [V]**, **actual measured current [A]**, calculated **power [W]**, and separate programmed setpoints. Change its sample interval (default 2 seconds) and graph range (1 h, 24 h, 48 h, All).
3. The red/blue graph uses independent **linear V / A axes**. Measurements are recorded continuously in independent SQLite files; **CSV** exports the full history. The browser monitor displays read-only electrical traces too.
4. TDK remote output controls start **locked**. To modify outputs, enter the correct model's nameplate V/A limits, verify a live reading, click **Unlock controls**, type **ARM**, and confirm each individual command. Nothing automatically switches on when a profile is loaded or after a restart. **This does not replace certified interlocks or the physical emergency-off system.**

**Model support is provisional until tested on your actual supply.** See [TDK-Lambda guide](docs/TDK_LAMBDA_GUIDE.md) for protocols, limitations and safety conditions.

## Portable profiles and adjustable panel layout

- The **Profiles** button provides **Save current / Load selected / Import / Export / Delete**. Profiles are named JSON files in `data/profiles/`, separate from the replaceable program files, and survive an ordinary update, reinstall or uninstall.
- Profiles restore controller types and names, serial settings, addresses, applicable instrument and graph preferences, alerts, theme (embedded JSON), and panel order. Select **Columns (1/2/3)** and use the **Move ↑ / ↓** controls to rearrange panels before saving.
- If a USB serial adapter has a different name on another computer, the loader can **remap the saved port** to the new port. Unavailable devices are retained in the profile without connecting.
- For safety, profiles do **not** contain passwords, TDK armed/output states or previous NGC3 emission requests. Imported settings disable unattended remote controls. Loading a profile never changes the supply output automatically.

See [Profile guide](docs/PROFILES.md) for paths, migration and portability.

## Using the application

1. Select the type of **device** and its **serial port**. A Pfeiffer TPG 262 or Agilent 4UHV needs one panel per physical controller, not one per pressure channel.
2. Set the matching baud rate. For EpiMax PVCi, also select its Modbus address and the gauge units shown on the hardware.
3. Click **Add** and compare displayed pressures with the physical front panels. Do not trust unknown/invalid units or statuses as a measurement.
4. Open **Options** for graph range, logging, theme, remote-control policy and software updates.
5. Open **Alerts / Email** for alerts and email settings. The SMTP password is not stored in `config.json`; supply it at runtime or with `UHV_SENTINEL_SMTP_PASSWORD` environment variable (legacy `NGC3_SMTP_PASSWORD` also works).
6. Open **Browser monitor** to get the address of the read-only local dashboard, and **Help** for an in-app README and links to instrument manuals.

For themes, place a `*theme*.json` in your persistent application `data/themes/` directory (or beside `uhv_sentinel.py` for built-in/example themes), then select it under **Options → Theme file**. The included `Unibasel_theme.json` and `example_theme.json` are starting points. Themes stored in `data/themes/` are preserved across normal updates, reinstallations and uninstalls. Profiles can also embed a selected theme for portable restoration.

**Network warning:** The browser dashboard has no login or TLS. Use only on a trusted private network, and do not forward its port to the public internet.

## Developer setup and tests

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python uhv_sentinel.py
```

On Windows activate with `.venv\Scripts\activate` and replace `python3` with `py -3`.

### Source layout

```text
uhv_sentinel.py          Main Tk GUI, graphs, logging, local dashboard, Options/Update
vacuum_extra_devices.py  Agilent and EpiMax serial readers
impac_pyrometer.py      IMPAC reader
tdk_lambda.py          Genesys/Z+ serial driver, read-only polling & gated commands
tdk_logging.py         Separate SQLite/CSV electrical measurement history
tdk_panel.py           TDK GUI, voltage/current/power plots & controls
profiles.py            Portable profile serialization, sanitization & themes
CHANGELOG.md           Version history
updater.py               GitHub release checker, SHA-256 validation, staged/rollback update
installer.py             Per-user installation and uninstallation
app_paths.py             Per-platform installation/data paths
version.py               Application version
release_config.json      GitHub release repository and asset names
scripts/                 Build-release and optional old-data import scripts
tests/                   Protocol and updater tests
.github/workflows/       Cross-platform CI and tagged-release publishing
docs/                    Hardware and publishing documentation
```

To publish your own project to GitHub and enable working updates, follow **[docs/RELEASING.md](docs/RELEASING.md)**. For a new release, change `version.py` and push a matching `vX.Y.Z` tag. GitHub Actions automatically builds the ZIP and checksum assets.

## Limits / credits

- New Agilent and EpiMax drivers have been verified with **simulated protocol responses** and unit tests, **not with physical hardware**.
- EpiMax support is currently limited to the documented **PVCi** register map, not generic RJ45 Ethernet or an unverified PVCiDuo second gauge.
- No high-voltage/pump/emission write commands are exposed by the new Agilent/EpiMax readers. The **legacy AML NGC3 controls remain available** if enabled.
- This open-source repository does not redistribute vendor PDF manuals. The GUI offers the vendor links instead.

Refer to [the instrument wiring guide](docs/DEVICE_CONNECTION_GUIDE.md) and the original vendor manuals before connecting any high-voltage or serial controller. See [LICENSE](LICENSE) for source code license.
