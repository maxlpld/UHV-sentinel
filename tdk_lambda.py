"""TDK-Lambda Genesys (legacy serial) and Z+ (SCPI serial) instruments.

The driver performs *queries only* during connection and polling. Output-changing
commands are only exposed through explicit methods called by an armed GUI.
This module does not automatically enable outputs or restore setpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import threading
import time
from typing import Optional

import serial

GENESYS = "Genesys (legacy GEN RS232)"
ZPLUS = "Z+ (SCPI RS232 / USB-serial)"
TDK_PROTOCOLS = (GENESYS, ZPLUS)


@dataclass(frozen=True)
class TDKReading:
    timestamp: float
    voltage_v: float
    current_a: float
    voltage_set_v: Optional[float] = None
    current_set_a: Optional[float] = None
    output_state: str = "unknown"
    mode: str = "unknown"
    identity: str = ""
    @property
    def power_w(self) -> float:
        return self.voltage_v * self.current_a
    @property
    def ok(self) -> bool:
        return math.isfinite(self.voltage_v) and math.isfinite(self.current_a)


def parse_number(value: str) -> float:
    text = str(value).strip()
    # Reject supply errors, empty results, unit strings and non-finite numbers.
    if not re.fullmatch(r"[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+\-]?\d+)?", text):
        raise ValueError(f"Invalid TDK numeric reply: {text!r}")
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("Non-finite TDK reading")
    return number


def validate_setpoint(value: float, maximum: float, unit: str) -> float:
    value, maximum = float(value), float(maximum)
    if not math.isfinite(value) or not math.isfinite(maximum) or maximum <= 0:
        raise ValueError(f"Provide a finite positive {unit} hardware limit")
    if not (0 <= value <= maximum):
        raise ValueError(f"{unit} setpoint must be between 0 and {maximum:g}")
    return value


class TDKLambdaDevice:
    """One supply over a physically verified, correctly wired serial port."""

    def __init__(self, port: str, baudrate: int = 9600, *, protocol: str = GENESYS, address: int = 6):
        if protocol not in TDK_PROTOCOLS:
            raise ValueError(f"Unsupported TDK model/protocol: {protocol}")
        if not 0 <= int(address) <= 30:
            raise ValueError("Genesys serial address must be 0–30")
        self.port = port
        self.baudrate = int(baudrate)
        self.protocol = protocol
        self.address = int(address)
        self.ser: Optional[serial.Serial] = None
        self.lock = threading.RLock()
        self.identity = ""

    def open(self) -> None:
        with self.lock:
            if self.ser is not None:
                return
            handle = serial.Serial(self.port, self.baudrate, bytesize=serial.EIGHTBITS,
                                   parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                                   timeout=1.0, write_timeout=1.0)
            self.ser = handle
            try:
                if self.protocol == GENESYS:
                    reply = self._query("ADR " + str(self.address))
                    if reply.upper() != "OK":
                        raise RuntimeError(f"Genesys address not accepted: {reply!r}")
                    self.identity = self._query("IDN?")
                    if not ("GEN" in self.identity.upper() or "LAMBDA" in self.identity.upper()):
                        raise RuntimeError(f"Not recognized as a Genesys supply: {self.identity!r}")
                else:
                    self.identity = self._query("*IDN?")
                    if "TDK" not in self.identity.upper() and "LAMBDA" not in self.identity.upper():
                        raise RuntimeError(f"Unexpected SCPI device identity: {self.identity!r}")
            except Exception:
                self.close()
                raise

    def close(self) -> None:
        with self.lock:
            old, self.ser = self.ser, None
            if old is not None:
                old.close()

    def _query(self, command: str) -> str:
        if self.ser is None:
            raise RuntimeError("TDK supply is not connected")
        terminator = b"\r" if self.protocol == GENESYS else b"\n"
        self.ser.reset_input_buffer()
        self.ser.write(command.encode("ascii") + terminator)
        self.ser.flush()
        # Z+ may reply CRLF; both protocols can be terminated by CR.
        response = self.ser.read_until(b"\r" if self.protocol == GENESYS else b"\n", 256)
        clean = response.decode("ascii", "replace").strip()
        if not clean:
            raise TimeoutError(f"No reply from TDK to {command}")
        if re.fullmatch(r"(?:E\d{2}|C\d{2}|ERR(?:OR)?[^\r\n]*)", clean, flags=re.I):
            raise RuntimeError(f"TDK command {command}: {clean}")
        return clean

    def _send(self, command: str) -> None:
        """Only called by explicit, confirmation-gated application actions."""
        if self.ser is None:
            raise RuntimeError("TDK supply is not connected")
        termination = b"\r" if self.protocol == GENESYS else b"\n"
        self.ser.write(command.encode("ascii") + termination)
        self.ser.flush()
        if self.protocol == GENESYS:
            reply = self.ser.read_until(b"\r", 256).decode("ascii", "replace").strip()
            if reply.upper() != "OK":
                raise RuntimeError(f"TDK rejected {command}: {reply!r}")
        # SCPI writes do not typically produce a direct response.

    def read_status(self) -> TDKReading:
        with self.lock:
            if self.protocol == GENESYS:
                v = parse_number(self._query("MV?"))
                i = parse_number(self._query("MC?"))
                vs = parse_number(self._query("PV?"))
                cs = parse_number(self._query("PC?"))
                mode = self._query("MODE?")
                state = "OFF" if mode.upper() == "OFF" else "ON" if mode.upper() in {"CV", "CC"} else "unknown"
            else:
                v = parse_number(self._query("MEAS:VOLT?"))
                i = parse_number(self._query("MEAS:CURR?"))
                vs = parse_number(self._query("VOLT?"))
                cs = parse_number(self._query("CURR?"))
                state_reply = self._query("OUTP?").upper()
                state = "ON" if state_reply in ("1", "ON") else "OFF" if state_reply in ("0", "OFF") else "unknown"
                mode = "SCPI"
            return TDKReading(time.time(), v, i, vs, cs, state, mode, self.identity)

    def set_voltage(self, volts: float, max_volts: float) -> None:
        value = validate_setpoint(volts, max_volts, "Voltage (V)")
        with self.lock:
            self._send(f"PV {value:.6g}" if self.protocol == GENESYS else f"VOLT {value:.6g}")

    def set_current(self, amps: float, max_amps: float) -> None:
        value = validate_setpoint(amps, max_amps, "Current (A)")
        with self.lock:
            self._send(f"PC {value:.6g}" if self.protocol == GENESYS else f"CURR {value:.6g}")

    def set_output(self, enabled: bool) -> None:
        with self.lock:
            self._send(f"OUT {1 if enabled else 0}" if self.protocol == GENESYS
                       else f"OUTP {1 if enabled else 0}")
