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
    if not 0x80 <= frame[1] <= 0x9f:
        raise ValueError('Unexpected Agilent response address')
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
        self._diag_index = 0
        self._diag_backoff = {}
        self._failed_sweeps = 0
        self._last_reconnect = 0.0
        self._reconnects = 0
        self._last_good = 0.0
        self._last_command = 0.0

    def open(self):
        with self.lock:
            if self.ser and self.ser.is_open:
                return
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.25,
                                     write_timeout=1.0, bytesize=8, parity='N',
                                     stopbits=1, xonxoff=False, rtscts=False, dsrdtr=False)
            self.ser.reset_input_buffer()
            self._last_meta = 0.0

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
                crc = bytearray()
                while len(crc) < 2 and time.monotonic() < deadline:
                    crc.extend(ser.read(2 - len(crc)))
                if len(crc) != 2:
                    raise TimeoutError('Incomplete Agilent CRC')
                packet.extend(crc)
                return bytes(packet)
        raise TimeoutError('Truncated Agilent response')

    def query_window(self, window: int) -> str:
        with self.lock:
            if not self.ser or not self.ser.is_open:
                raise RuntimeError('Agilent serial port is closed')
            delay = 0.08 - (time.monotonic() - self._last_command)
            if delay > 0:
                time.sleep(delay)
            self.ser.reset_input_buffer()
            self.ser.write(agilent_read_frame(window))
            self.ser.flush()
            self._last_command = time.monotonic()
            return parse_agilent_response(self._read_frame(), window)

    def _optional_window(self, window: int, name: str):
        try:
            return (name, self.query_window(window))
        except (TimeoutError, ValueError, RuntimeError, OSError, serial.SerialException) as exc:
            return (name, f'unavailable: {exc}')

    def _refresh_metadata(self):
        # Never sweep optional registers before a pressure acquisition.
        code = int(self.query_window(600))
        if code not in (0, 1, 2):
            raise ValueError(f'Unknown Agilent pressure unit code {code}')
        self._units = {0: 'Torr', 1: 'mBar', 2: 'Pa'}[code]
        self._last_meta = time.monotonic()
        self._diagnostics.pop('Metadata error', None)

    def _poll_one_diagnostic(self):
        # One optional register per acquisition; unsupported ones back off.
        fields = ((800, 'Fan temperature (°C)'), (821, 'Channel 2 Current (A)')) + ((108,'Configured baud rate'), (205,'Controller status'),
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
        now = time.monotonic()
        for _ in range(len(fields)):
            window, name = fields[self._diag_index % len(fields)]
            self._diag_index = (self._diag_index + 1) % len(fields)
            if now < self._diag_backoff.get(window, 0.0):
                continue
            _, value = self._optional_window(window, name)
            self._diagnostics[name] = value
            if value.startswith('unavailable:'):
                self._diag_backoff[window] = now + 300
            break

    def _read_pressure_with_retry(self, window: int) -> float:
        problem = None
        for _ in range(2):
            try:
                text = self.query_window(window)
                value = float(text)
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(f'Invalid pressure: {text!r}')
                return value
            except (TimeoutError, ValueError, RuntimeError, OSError, serial.SerialException) as exc:
                problem = exc
        raise ValueError(f'Window {window} failed twice: {problem}')

    def _recover_after_failed_sweeps(self):
        now = time.monotonic()
        if self._failed_sweeps < 3 or (self._last_reconnect and now - self._last_reconnect < 12):
            return
        self._last_reconnect = now
        self._reconnects += 1
        try:
            self.close()
            self.open()
            self._diagnostics['Recovery'] = 'Serial port reopened after repeated pressure failures'
        except (OSError, RuntimeError, serial.SerialException) as exc:
            self._diagnostics['Recovery'] = f'Reconnect failed: {exc}'

    def read_status(self) -> ExtendedStatus:
        with self.lock:
            if time.monotonic() - self._last_meta > 120 or self._last_meta == 0:
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
                    p = self._read_pressure_with_retry(base + 2)
                except (TimeoutError, ValueError, serial.SerialException) as exc:
                    diags[f'{key} pressure'] = f'unavailable: {exc}'
                    p = None
                else:
                    good += 1
                # Diagnostics are polled later, after both pressures.
                st.gauges[key] = ReadOnlyGauge(key, ch, 0 if p is not None else 5, p,
                                               self._units, sensor_id='Ion pump')
            if good:
                self._failed_sweeps = 0
                self._last_good = time.time()
                self._poll_one_diagnostic()
            else:
                self._failed_sweeps += 1
                self._recover_after_failed_sweeps()
            diags.update(self._diagnostics)
            diags['Consecutive failed pressure polls'] = self._failed_sweeps
            diags['Serial reconnects'] = self._reconnects
            diags['Last valid pressure'] = (time.strftime('%Y-%m-%d %H:%M:%S',
                time.localtime(self._last_good)) if self._last_good else 'never')
            st.diagnostics = diags
            st.ok = good > 0
            failed = [f'{name}: {value}' for name, value in diags.items()
                      if name.endswith(' pressure') and str(value).startswith('unavailable:')]
            st.parse_error = '; '.join(failed) if failed else (
                '' if st.ok else 'No valid Agilent channel pressure returned')
            st.raw = 'Agilent 4UHV Window Protocol (pressure-first, read-only)'
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
