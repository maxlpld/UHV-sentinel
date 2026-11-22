# TDK-Lambda monitoring and cautious remote control

UHV Sentinel v0.2.0 adds a measured voltage/current/power panel, continuous SQLite history, CSV export, and deliberately limited manual output controls.

## Before connecting

**Confirm the exact TDK-Lambda model on the label and its communication protocol.** The software currently supports these documented *serial* command families, not every TDK supply:

| Series | App selection | Typical command set | Connector |
| --- | --- | --- | --- |
| Older Genesys GEN series | `Genesys (legacy GEN RS232)` | `ADR n` (address selection), `IDN?`, `MV?`, `MC?`, `PV?`, `PC?`, `MODE?` | Model-specific RS232/RS485, often RJ45-format serial |
| Z+ in SCPI mode | `Z+ (SCPI RS232 / USB-serial)` | `*IDN?`, `MEAS:VOLT?`, `MEAS:CURR?`, `VOLT?`, `CURR?`, `OUTP?` | RS232/USB adapter as specified by your unit |

A RJ45-shaped port **does not necessarily support Ethernet**. Do not plug a serial RJ45 port into an Ethernet switch. Obtain the correct manufacturer pinout, isolated USB-to-serial adapter if appropriate, and ensure the instrument is configured for RS232 (not RS485 or incompatible addressing). Match the physical baud rate. Not all units use 9600 baud. Some Z+ units support both GEN and SCPI dialects; select **SCPI** on the front panel when using the Z+ driver.

**Source manuals** (please verify against the exact device revision):
- [TDK-Lambda Genesys GEN manual](https://manualzz.com/doc/7948715/genesys-gen40-85-user-manual)
- [TDK-Lambda Z+ manual and command protocol](https://digitaler.cld.bz/tdk2/136)

## How to use

1. With an appropriate serial cable connected, in the GUI choose **Device → TDK-Lambda power supply**.
2. Select the correct Genesys or Z+ command protocol, device address (for Genesys), serial port and baud rate. Name the panel, then click **Add**.
3. Compare the **measured** V and A displayed in UHV Sentinel against the physical front panel before trusting the graph. Note that voltage/current *setpoints* are shown separately from measured outputs.
4. Samples are stored in an independent weekly SQLite file named `tdk_measurements_YYYY-Www.sqlite3` (UTC week) under the persistent data folder. Default sampling is every **2 seconds**. Change the per-panel interval to 0.5–60 seconds to suit your experiment; actual throughput is limited by serial read time.
5. Select **1 h / 24 h / 48 h / All**. The plot uses two separate calibrated linear axes (red voltage, blue current). Power is calculated and displayed. Select **CSV** to export full logged history.
6. The read-only browser monitor also displays voltage and current curves. **Remote power controls are never available in the browser.**

## Power control safety

- **Initial state is monitoring-only.** Connecting, starting the application, loading profiles, and logging do not issue output-changing commands. Genesys address selection `ADR n` is needed to communicate and does not change the output.
- The app does **not** automatically apply voltage/current limits, run bakeout routines, ramp outputs, or turn on/off the output when a profile is loaded.
- To manually control a real output, enter **both nameplate V and A limits**, obtain a current reading, click **Unlock controls**, read the safety warning and type **ARM**.
- While armed, the permitted commands are **Apply voltage**, **Apply current limit**, **Output ON**, and **Output OFF**. Each one requires a separate confirmation. The app rejects negative, non-finite, or over-limit entered values.
- **Output ON** additionally checks the supply's most recently reported V/A setpoints against the entered limits. If it cannot verify them, it will not send Output ON.
- On communication error the GUI disarms. A restart or profile restore always disarms, regardless of what the profile contains.
- These checks are software protections, **not certified overcurrent/overtemperature/vacuum interlocks**. Hardware protections, local front-panel emergency shutoff, correct cable ratings, appropriate grounding, controlled ramp rates, personnel supervision and established UHV outgassing procedures are still essential.
- Some models switch into remote mode as soon as a setting is changed. Check that you can safely return control to the front panel. Do not send commands to a real heater/manipulator until a qualified user confirms the **exact model**, connector and output behavior.

## Limitations and validation

The code and mocked serial driver have been tested; **no real laboratory power supply was available for hardware-in-the-loop testing**. The program assumes the documented Genesys or Z+ serial mode. It does **not** currently support arbitrary models, LAN VISA/SCPI sockets, RS485 multipoint selection, auto-ramping, heating recipes, or a safety-rated shutoff. The hardware's `OUT` or `OUTP` commands do not replace emergency-stop wiring.
