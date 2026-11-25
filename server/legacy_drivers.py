"""Headless, read-only NGC3 and Pfeiffer protocol drivers.

Only '*S0' status requests and Pfeiffer PRX/UNI/TID queries are sent.
Never issue remote control, emission or HV commands.
"""
from __future__ import annotations
import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
import serial

UNITS = {'M':'mBar', 'T':'Torr', 'P':'Pa'}
UNIT_FACTOR = {'mBar':1.0, 'Torr':1.333223684, 'Pa':0.01}
GAUGES = {'1':'IG1','2':'Pirani 1','3':'Pirani 2','4':'Active Gauge','5':'IG2'}

@dataclass
class Gauge:
    key: str
    pressure_value: Optional[float]
    units: str
    status_code: int = 0
    sensor_id: str = ''
    @property
    def pressure_mbar(self):
        if self.status_code != 0 or self.pressure_value is None:
            return None
        if not math.isfinite(self.pressure_value) or self.pressure_value <= 0:
            return None
        return self.pressure_value * UNIT_FACTOR.get(self.units, 1.0)

@dataclass
class Status:
    ok: bool = False
    gauges: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    parse_error: str = ''

class NGC3ReadOnly:
    def __init__(self,port,baudrate=9600):
        self.port=port;self.baudrate=baudrate;self.ser=None;self.lock=threading.RLock()
    def open(self):
        with self.lock:
            if self.ser is None:
                self.ser=serial.Serial(self.port,self.baudrate,timeout=0.25,write_timeout=1,
                                       bytesize=8,parity='N',stopbits=1)
    def close(self):
        with self.lock:
            if self.ser is not None:
                try:self.ser.close()
                finally:self.ser=None
    def read_status(self):
        with self.lock:
            if self.ser is None:raise RuntimeError('NGC3 port closed')
            self.ser.reset_input_buffer();self.ser.write(b'*S0');self.ser.flush()
            end=time.monotonic()+1.8;buf=bytearray();last=time.monotonic()
            while time.monotonic()<end:
                b=self.ser.read(256)
                if b:buf.extend(b);last=time.monotonic()
                elif buf and time.monotonic()-last>.18:break
        s=Status();raw=bytes(buf);first=raw.find(b'G')
        if first<4:
            s.parse_error='NGC3 status missing gauge records';return s
        s.diagnostics={'state_byte':raw[first-4], 'error_byte':raw[first-3],
                       'relay_byte':raw[first-2]}
        p=first
        while p+17<=len(raw):
            if raw[p]!=ord('G'):
                p=raw.find(b'G',p+1)
                if p<0:break
                continue
            record=raw[p:p+17];p+=17
            label=GAUGES.get(chr(record[2]),'Gauge '+chr(record[2]))
            text=record[5:13].decode('ascii','ignore').strip().rstrip(',').strip()
            try:value=float(text)
            except (TypeError,ValueError):value=None
            unit=UNITS.get(chr(record[13]),'mBar')
            gauge_type=chr(record[1])
            s.gauges[label]=Gauge(label,value,unit,0 if value is not None else 5,gauge_type)
            s.diagnostics[label+' status_byte']=record[3]
            s.diagnostics[label+' error_byte']=record[4]
        s.ok=bool(s.gauges)
        if not s.ok:s.parse_error='NGC3 returned no parsable gauge records'
        return s

class PfeifferReadOnly:
    def __init__(self,port,baudrate=9600):
        self.port=port;self.baudrate=baudrate;self.ser=None;self.lock=threading.RLock()
        self.unit=None;self.ids=('','');self.last_meta=0
    def open(self):
        with self.lock:
            if self.ser is None:
                self.ser=serial.Serial(self.port,self.baudrate,timeout=.15,write_timeout=1,
                                       bytesize=8,parity='N',stopbits=1)
                self.ser.reset_input_buffer();self.ser.write(b'\x03');self.ser.flush()
                time.sleep(.08);self.ser.reset_input_buffer()
    def close(self):
        with self.lock:
            if self.ser is not None:
                try:self.ser.close()
                finally:self.ser=None
    def _line(self,timeout=1.4):
        end=time.monotonic()+timeout;buf=bytearray()
        while time.monotonic()<end:
            c=self.ser.read(1)
            if c:
                buf.extend(c)
                if c==b'\n':return bytes(buf)
        raise TimeoutError('Pfeiffer response timed out')
    def _query(self,command):
        with self.lock:
            if self.ser is None:raise RuntimeError('Pfeiffer port closed')
            if not re.fullmatch('[A-Z0-9]{3}',command):raise ValueError('Invalid mnemonic')
            self.ser.reset_input_buffer();self.ser.write(command.encode()+b'\r');self.ser.flush()
            ack=self._line()
            if not ack.startswith(b'\x06'):raise RuntimeError('No ACK from Pfeiffer '+command)
            self.ser.write(b'\x05');self.ser.flush()
            return self._line().decode('ascii','replace').strip()
    def read_status(self):
        with self.lock:
            if self.unit is None or time.monotonic()-self.last_meta>120:
                # Metadata should not suppress an otherwise usable pressure response.
                try:
                    self.unit={0:'mBar',1:'Torr',2:'Pa'}.get(int(self._query('UNI').split(',')[0]),'mBar')
                    ids=self._query('TID').split(',');self.ids=(ids+['',''])[:2]
                    self.last_meta=time.monotonic()
                except (TimeoutError,ValueError,RuntimeError,serial.SerialException):
                    self.unit=self.unit or 'mBar';self.last_meta=time.monotonic()
            s=Status();data=self._query('PRX').split(',')
            if len(data)!=4:s.parse_error='Unexpected PRX: '+repr(data);return s
            for idx,(code,value) in enumerate(((data[0],data[1]),(data[2],data[3])),1):
                try:status=int(code);pressure=float(value)
                except (ValueError,TypeError):status=5;pressure=None
                name='Channel '+str(idx)
                s.gauges[name]=Gauge(name,pressure,self.unit,status,self.ids[idx-1])
            s.ok=any(g.pressure_mbar is not None for g in s.gauges.values())
            if not s.ok:s.parse_error='No valid Pfeiffer channel pressure'
            return s
