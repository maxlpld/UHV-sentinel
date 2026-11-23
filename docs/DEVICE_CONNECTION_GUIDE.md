# Connection guide — UHV Sentinel

## Select device, serial port, and baud

| Device | GUI option | Default serial | Pressure channels |
| --- | --- | --- | --- |
| Pfeiffer TPG 262 | Pfeiffer DualGauge TPG 262 | 9600, RS232 | 1 and 2 |
| Agilent 4UHV 2x80W NEG | Agilent 4UHV 2x80W NEG | 9600/8N1, RS232 J2 | 1 and 2 |
| EpiMax PVCi | EpiMax PVCi (RJ45 serial) | 19200/8N1 Modbus RTU, address 1 | ion gauge 1 |
| TDK-Lambda Genesys (legacy) | TDK-Lambda power supply | Model-selected baud, 8N1 RS232, address default 6 | measured voltage/current/power |
| TDK-Lambda Z+ (SCPI) | TDK-Lambda power supply | Model-selected baud, 8N1 RS232/USB-serial | measured voltage/current/power |

**Each physical serial controller needs its own connected COM or /dev/ttyUSB port. One Pfeiffer or Agilent panel handles both channels of that controller.**

## Agilent J2: check the manual before connecting

The Agilent 4UHV User Manual (87-900-137-01 D) describes a 9-pin D-type serial connector (drawing in section 5, Serial) with:

- Controller pin 2: RS232 TX
- Controller pin 3: RS232 RX
- Controller pin 5: serial GND
- Controller pin 1: **+5 V output**. Do not connect to the PC's RS232 signal pins.
- Controller pins 6 and 8: RS485 A/B (not used by this RS232 driver).

The manual's RS232 wiring diagram shows 2↔2, 3↔3, 5↔5 between controller and a standard PC serial connector, **but verify whether your particular USB-RS232 adapter expects a straight-through or cross-wired cable before fabrication**. RS232 signalling is not USB-TTL, and nothing here should be connected to high-voltage output ports.

The GUI requests Windows Protocol numbers 812/822 for pressure, 810/820 for voltage, 811/821 for current, and associated read-only diagnostic windows. It does not enable, disable or set HV.

## EpiMax RJ45: RS232/RS485 serial, not Ethernet (documented PVCi/PVCiDuo)

Although its socket looks like Ethernet, these PVCi-series models use RS232/RS485 serial on RJ45 connectors. **Do not connect it to an Ethernet switch or computer network port**. For the documented EpiMax PVCi/9D232 serial cable, the PVC-side RJ45 pin assignments are:

- Pin 1: 0 V (signal return)
- Pin 2: RS232 receive (controller RX)
- Pin 3: RS232 transmit (controller TX)

Use the manufacturer's **PVCi-9D232** cable or a cable wired following the actual controller model and its manual, then a true USB-to-RS232 adapter if necessary. A standard RJ45 Ethernet cable may be useful for a PVCi-to-PVCi daisy chain, but **not** as an Ethernet/TCP-IP adapter.

The new EpiMax driver addresses the **PVCi ion gauge 1** through read-only Modbus function 23, register 0x9A. The unit (mBar, Torr, Pa) **must agree with the physical controller**. Its full model and firmware should be confirmed before any laboratory use. The other PVCiDuo ion gauge is intentionally unsupported pending its exact register map.

## Experimental EpiMax TCP/IP option (v0.0.2)

The EpiMax connection selector now offers **Ethernet (Modbus TCP)** for
genuinely network-capable instruments and **Ethernet (RTU/TCP gateway)** for a
separate, correctly wired and powered transparent serial-to-TCP gateway.
Supply the verified IP address and TCP port (502 is the conventional Modbus
TCP port; gateway ports vary). The protocol still uses the PVCi gauge-1
read-only pressure register map and has not been physically validated.

**An ASIX AX88772 USB-to-Ethernet adapter is a network card, not an RS232 or
RS485 converter.** Windows showing its network link as Disconnected means it
does not detect Ethernet link. The manufacturer documents the RJ45 sockets on
PVCi/PVCiDuo and PVCuni/PVCduo as RS232/RS485; do not connect those sockets
to a normal Ethernet port. Software cannot change the electrical pinout.

For saved profiles, TCP endpoints are stored as tcp://HOST:PORT or
rtu+tcp://HOST:PORT and no COM-port remapping is required. The optional
network paths do not enable gauge emission or alter high-voltage settings.

## TDK-Lambda power supplies

TDK serial wiring and commands vary substantially across products. **Check the exact model number, serial port configuration, physical pinout and rated voltage/current before connecting.** A voltage/current display or RJ-style connector does not by itself identify the protocol. UHV Sentinel has separate read-only polling and explicit, confirmation-gated remote controls for legacy **Genesys** and **Z+ in SCPI mode**; it does **not** support every TDK-Lambda unit or power-supply interface. Remote controls always begin locked after connecting or loading a profile. Consult [TDK-Lambda connection guide](TDK_LAMBDA_GUIDE.md).

## Installation commands

Linux / macOS:

```bash
cd /path/to/UHV-Sentinel
bash INSTALL_LINUX_MAC.sh
```

Windows: double-click `INSTALL_WINDOWS.bat` from the extracted project folder. If you already have an older installation, extract to a new directory first and test without overwriting it. The new shortcut is named **UHV Sentinel**.

## Before trusting the readings

1. Verify the actual controller model and its communication pinout.
2. Use the correct USB-to-RS232 adapter and serial cable; do not guess from connector shape.
3. Configure the physical serial baud (and EpiMax Modbus address/unit).
4. Compare each channel's live pressure against its local instrument display.
5. Confirm labels, channels, units, and diagnostics.
6. Treat software graphs and email alarms as informative only; retain all hardware safety protection/interlocks.

## Reference manuals

- Agilent 4UHV controller: https://www.agilent.com/cs/library/usermanuals/public/4UHV_Controller_User_Manual.pdf
- EpiMax PVCi/PVCiDuo: https://wiki.kip.uni-heidelberg.de/KIPwiki/images/7/7e/ZIGDUO.pdf
- PVCi Modbus implementation reference: https://github.com/CINF/PyExpLabSys/blob/master/PyExpLabSys/drivers/epimax.py

**Hardware validation status:** Packet encoding, CRC, simulated serial responses, graphical dashboard and history traces were tested; no physical lab instrument was connected during this patch.
