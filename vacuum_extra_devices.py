"""Read-only drivers for Agilent 4UHV and EpiMax PVCi-series serial controllers.

Only passive requests are emitted. No controller mode, HV, gauge emission, or
interlock-changing write commands exist in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import struct
import threading
import time
from typing import Dict, Optional

import serial


PRESSURE_UNITS = {'mBar': 1.0, 'Torr': 1.333223684, 'Pa': 0.01}


@dataclass
class ReadOnlyGauge:
    key: str
    channel: int
    status_code: int
    pressure_value: Optional[float]
    units: str
    sensor_id: str = ''

    @property
    def pressure_text(self):
        return f'{self.pressure_value:.5e}' if self.pressure_value is not None else ''

    @property
    def pressure_display(self):
        if self.pressure_value is None:
            return self.sensor_id or 'Not available'
        return f'{self.pressure_value:.5e} {self.units}'

    @property
    def pressure_mbar(self):
        v = self.pressure_value
        if self.status_code != 0 or v is None or not math.isfinite(v) or v <= 0:
            return None
        return v * PRESSURE_UNITS[self.units]


@dataclass
class ExtendedStatus:
    raw: str = ''
    ok: bool = False
    gauges: Dict[str, ReadOnlyGauge] = field(default_factory=dict)
    units: str = 'mBar'
    timestamp: float = field(default_factory=time.time)
    parse_error: str = ''
    diagnostics: Dict[str, object] = field(default_factory=dict)


def agilent_crc(body: bytes) -> bytes:
    """Manual: XOR all bytes *after* STX, including ETX; uppercase hex."""
    crc = 0
    for b in body:
        crc ^= b
    return f'{crc:02X}'.encode('ascii')


def agilent_read_frame(window: int) -> bytes:
    if not 0 <= window <= 999:
        raise ValueError('Agilent window outside 000..999')
    body = b'\x80' + f'{window:03d}'.encode('ascii') + b'0\x03'
    return b'\x02' + body + agilent_crc(body)


def parse_agilent_response(frame: bytes, expected_window: int) -> str:
    if len(frame) < 8 or frame[0] != 2 or frame[-3] != 3:
        raise ValueError(f'Bad Agilent packet framing: {frame.hex(" ")}')
    if frame[1] != 0x80:
        raise ValueError('Unexpected Agilent RS232 address')
    if frame[-2:].upper() != agilent_crc(frame[1:-2]):
        raise ValueError('Agilent CRC mismatch')
    payload = frame[2:-3]
    if payload and payload[0] in (0x06, 0x15, 0x32, 0x33, 0x35):
        raise ValueError(f'Agilent error reply code 0x{payload[0]:02X}')
    if len(payload) < 5 or payload[:3] != f'{expected_window:03d}'.encode('ascii'):
        raise ValueError(f'Agilent returned wrong window: {payload!r}')
    if payload[3:4] != b'0':
        raise ValueError('Agilent did not return a window read result')
    return payload[4:].decode('ascii').strip()


class Agilent4UHVDevice:
    """4UHV RS232 Window Protocol, 9600/8N1, factory RS232 addr 0x80.

    Per manual 87-900-137-01(D): pressure 812/822, current 811/821,
    voltage 810/820; 600 pressure units; 800-804 temperatures/status.
    NEG means negative-polarity output, not a non-evaporable getter channel.
    """

    def __init__(self, port: str, baudrate: int = 9600):
        self.port, self.baudrate = port, baudrate
        self.ser = None
        self.lock = threading.RLock()
        self._units = 'mBar'
        self._last_meta = 0.0
        self._diagnostics = {}

    def open(self):
        with self.lock:
            if self.ser and self.ser.is_open:
                return
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.25,
                                     write_timeout=1.0, bytesize=8, parity='N',
                                     stopbits=1, xonxoff=False, rtscts=False, dsrdtr=False)
            self.ser.reset_input_buffer()

    def close(self):
        with self.lock:
            if self.ser:
                self.ser.close()
                self.ser = None

    def _read_frame(self) -> bytes:
        ser = self.ser
        if not ser or not ser.is_open:
            raise RuntimeError('Agilent serial port is closed')
        deadline = time.monotonic() + 1.5
        # Read through STX, then through ETX and its 2 ASCII CRC characters.
        while time.monotonic() < deadline:
            if ser.read(1) == b'\x02':
                break
        else:
            raise TimeoutError('No Agilent STX response')
        packet = bytearray(b'\x02')
        while len(packet) < 40 and time.monotonic() < deadline:
            b = ser.read(1)
            if not b:
                continue
            packet.extend(b)
            if b == b'\x03':
                suffix = ser.read(2)
                if len(suffix) != 2:
                    raise TimeoutError('Incomplete Agilent CRC')
                packet.extend(suffix)
                return bytes(packet)
        raise TimeoutError('Truncated Agilent response')

    def query_window(self, window: int) -> str:
        with self.lock:
            if not self.ser or not self.ser.is_open:
                raise RuntimeError('Agilent serial port is closed')
            self.ser.reset_input_buffer()
            self.ser.write(agilent_read_frame(window))
            self.ser.flush()
            return parse_agilent_response(self._read_frame(), window)

    def _optional_window(self, window: int, name: str):
        try:
            return (name, self.query_window(window))
        except (TimeoutError, ValueError, serial.SerialException):
            return (name, 'unavailable')

    def _refresh_metadata(self):
        code = int(self.query_window(600))
        if code not in (0, 1, 2):
            raise ValueError(f'Unknown Agilent pressure unit code {code}')
        self._units = {0: 'Torr', 1: 'mBar', 2: 'Pa'}[code]
        fields = ((108,'Configured baud rate'), (205,'Controller status'),
                  (206,'Controller error code'), (319,'Controller model'),
                  (323,'Controller serial'), (503,'RS485 address'),
                  (504,'Serial interface select'), (505,'Error channel selection'),
                  (601,'Autostart / operating mode raw'),
                  (602,'Pump protection settings raw'),
                  (603,'Fixed/step configuration raw'),
                  (800,'Fan temperature (°C)'), (801,'HV1 temperature (°C)'),
                  (802,'HV2 temperature (°C)'), (803,'Interlock status'),
                  (804,'Setpoint status'), (610,'Pump model CH1'),
                  (612,'Power limit CH1 (W)'), (613,'Target voltage CH1 (V)'),
                  (614,'Protect current CH1 (mA)'), (615,'Setpoint CH1'),
                  (620,'Pump model CH2'), (622,'Power limit CH2 (W)'),
                  (623,'Target voltage CH2 (V)'), (624,'Protect current CH2 (mA)'),
                  (625,'Setpoint CH2'))
        self._diagnostics = dict(self._optional_window(num, name) for num, name in fields)
        self._last_meta = time.monotonic()

    def read_status(self) -> ExtendedStatus:
        with self.lock:
            if time.monotonic() - self._last_meta > 60 or self._last_meta == 0:
                # Metadata is optional, measurements remain available if some
                # firmware variants disable individual diagnostic windows.
                try:
                    self._refresh_metadata()
                except (TimeoutError, ValueError, serial.SerialException) as exc:
                    self._diagnostics['Metadata error'] = str(exc)
                    self._last_meta = time.monotonic()
            st = ExtendedStatus(units=self._units)
            diags = dict(self._diagnostics)
            good = 0
            for ch in (1, 2):
                base = 810 + 10 * (ch - 1)
                key = f'Channel {ch}'
                # Not every 4UHV has both channels populated; no fake pressure
                # reading is returned for disabled/unconfigured channels.
                try:
                    p_text = self.query_window(base + 2)
                    p = float(p_text)
                    if not math.isfinite(p) or p <= 0:
                        raise ValueError(f'Invalid pressure: {p_text!r}')
                except (TimeoutError, ValueError, serial.SerialException) as exc:
                    diags[f'{key} pressure'] = f'unavailable: {exc}'
                    p = None
                else:
                    good += 1
                diags[f'{key} HV on/off'] = self._optional_window(10 + ch, f'{key} HV on/off')[1]
                for offset, field_name, unit in ((0, 'Voltage', 'V'), (1, 'Current', 'A')):
                    name = f'{key} {field_name} ({unit})'
                    diags[name] = self._optional_window(base + offset, name)[1]
                st.gauges[key] = ReadOnlyGauge(key, ch, 0 if p is not None else 5, p,
                                               self._units, sensor_id='Ion pump')
            st.diagnostics = diags
            st.ok = good > 0
            st.parse_error = '' if st.ok else 'No valid Agilent channel pressure returned'
            st.raw = 'Agilent 4UHV Window Protocol (read-only)'
            return st


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def epimax_fc23_read_request(slave_address: int, start: int, count: int = 2) -> bytes:
    """Modbus FC23 read/write registers, with *zero* write registers."""
    if not 1 <= slave_address <= 247 or not 0 <= start <= 0xFFFF or not 1 <= count <= 32:
        raise ValueError('Invalid Epimax Modbus slave/read register settings')
    frame = bytes([slave_address, 0x17]) + struct.pack('>HHHHB', start, count, start, 0, 0)
    return frame + struct.pack('<H', modbus_crc16(frame))


def parse_epimax_fc23_response(packet: bytes, slave_address: int, count: int = 2) -> bytes:
    if len(packet) < 5 or packet[0] != slave_address:
        raise ValueError('Bad Epimax Modbus reply address/length')
    if struct.unpack('<H', packet[-2:])[0] != modbus_crc16(packet[:-2]):
        raise ValueError('Epimax Modbus CRC mismatch')
    if packet[1] & 0x80:
        raise ValueError(f'Epimax Modbus exception code {packet[2]}')
    if packet[1] != 0x17 or packet[2] != count * 2 or len(packet) != 5 + 2 * count:
        raise ValueError('Unexpected Epimax Modbus reply format')
    return packet[3:-2]


class EpimaxPVCDevice:
    """EpiMax PVCi Modbus RTU over RJ45-wired RS232/RS485 (NOT Ethernet).

    Uses FC23 & register 0x9A pressure documented by PyExpLabSys driver;
    default 19200 baud/8N1. Selected pressure unit must match front panel.
    PVCiDuo channel 2 is deliberately NOT guessed (register undocumented).
    """

    def __init__(self, port: str, baudrate: int = 19200, address: int = 1,
                 pressure_units: str = 'mBar'):
        if pressure_units not in PRESSURE_UNITS:
            raise ValueError('Choose mBar, Torr, or Pa')
        if not 1 <= address <= 99:
            raise ValueError('EpiMax address should be 1–99')
        self.port, self.baudrate, self.address = port, baudrate, address
        self.pressure_units = pressure_units
        self.ser = None
        self.lock = threading.RLock()
        self._metadata = {}
        self._last_meta = 0.0

    def open(self):
        with self.lock:
            if self.ser and self.ser.is_open:
                return
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.3,
                                     write_timeout=1.0, bytesize=8, parity='N',
                                     stopbits=1, xonxoff=False, rtscts=False, dsrdtr=False)
            self.ser.reset_input_buffer()

    def close(self):
        with self.lock:
            if self.ser:
                self.ser.close()
                self.ser = None

    def read_registers(self, start: int, count: int = 2) -> bytes:
        with self.lock:
            ser = self.ser
            if not ser or not ser.is_open:
                raise RuntimeError('EpiMax serial port is closed')
            ser.reset_input_buffer()
            ser.write(epimax_fc23_read_request(self.address, start, count))
            ser.flush()
            head = ser.read(3)
            if len(head) != 3:
                raise TimeoutError('EpiMax did not respond; check RJ45 serial wiring, baud and Modbus address')
            if head[1] & 0x80:
                tail = ser.read(2)
            else:
                if head[2] > 64:
                    raise ValueError('Invalid Epimax byte count')
                tail = ser.read(head[2] + 2)
            return parse_epimax_fc23_response(head + tail, self.address, count)

    def read_float(self, start: int) -> float:
        # PyExpLabSys PVCi reference: minimalmodbus.FLOAT_ENDIANNESS = '<'
        return struct.unpack('<f', self.read_registers(start))[0]

    def read_status(self) -> ExtendedStatus:
        with self.lock:
            p = self.read_float(0x9A)  # common PVCi register: gauge 1 pressure
            if not math.isfinite(p) or p <= 0:
                raise ValueError(f'Invalid EpiMax gauge 1 pressure {p!r}; confirm pressure unit and firmware')
            st = ExtendedStatus(ok=True, units=self.pressure_units,
                                raw='EpiMax MODBUS RTU FC23, register 0x9A')
            st.gauges['Ion gauge 1'] = ReadOnlyGauge('Ion gauge 1', 1, 0, p,
                                                    self.pressure_units, 'PVCi gauge')
            if time.monotonic() - self._last_meta > 60 or not self._last_meta:
                diagnostics = {}
                # Optional diagnostic registers; a failure must not suppress pressure.
                for addr, label in ((0x00, 'Controller ID'), (0x10, 'Unit name'),
                                    (0x88, 'Gauge status raw'),
                                    (0x42, 'Slot A ID raw'), (0x44, 'Slot B ID raw')):
                    try:
                        raw = self.read_registers(addr)
                        diagnostics[label] = raw.hex(' ').upper()
                    except (ValueError, TimeoutError, serial.SerialException) as exc:
                        diagnostics[label] = f'unavailable ({exc})'
                self._metadata = diagnostics
                self._last_meta = time.monotonic()
            st.diagnostics = dict(self._metadata)
            return st
