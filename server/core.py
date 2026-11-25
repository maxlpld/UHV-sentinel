"""Headless UHV Sentinel service: independent systems, workers and SQLite history.

No instrument-changing commands are available through the server API.
"""
from __future__ import annotations
import ipaddress
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

DEVICE_TYPES = {
    'pfeiffer': 'Pfeiffer DualGauge TPG262 (RS232)',
    'agilent': 'Agilent 4UHV (RS232)',
    'epimax_serial': 'EpiMax PVCi (RJ45 serial)',
    'epimax_modbus_tcp': 'EpiMax (verified Modbus TCP)',
    'epimax_rtu_gateway': 'EpiMax (RS232/485-to-TCP gateway)',
    'tdk_genesys': 'TDK-Lambda Genesys (read-only)',
    'tdk_zplus': 'TDK-Lambda Z+ (read-only)',
    'impac': 'IMPAC pyrometer (read-only)',
    'ngc3': 'AML NGC3 (read-only)',
}
TCP_TYPES = {'epimax_modbus_tcp','epimax_rtu_gateway'}
SLUG = re.compile(r'^[a-z][a-z0-9-]{1,39}$')


def validate_system(name: str, slug: str) -> tuple[str,str]:
    name=str(name).strip();slug=str(slug).strip().lower()
    if not (1<=len(name)<=72) or not SLUG.fullmatch(slug) or slug.endswith('-'):
        raise ValueError('System name must be 1–72 characters; slug 2–40 lowercase letters, digits or hyphens')
    return name,slug


def validate_device(d: dict) -> dict:
    if not isinstance(d,dict): raise ValueError('Invalid instrument configuration')
    kind=str(d.get('type',''))
    if kind not in DEVICE_TYPES: raise ValueError('Unsupported instrument type')
    name=str(d.get('name','')).strip()
    if not (1<=len(name)<=72): raise ValueError('Instrument name must be 1–72 characters')
    cfg={'type':kind,'name':name,'enabled':bool(d.get('enabled',True)),
         'interval':float(d.get('interval',5))}
    if not math.isfinite(cfg['interval']) or not 2<=cfg['interval']<=120:
        raise ValueError('Polling interval must be between 2 and 120 seconds')
    if kind in TCP_TYPES:
        host=str(d.get('host','')).strip()
        try: ipaddress.ip_address(host)
        except ValueError:raise ValueError('Use a literal IP address for network instruments') from None
        port=int(d.get('tcp_port',502))
        if not 1<=port<=65535:raise ValueError('TCP port must be 1–65535')
        cfg.update(host=host,tcp_port=port,address=int(d.get('address',1)))
    else:
        port=str(d.get('port','')).strip()
        # Limit filesystem device access to actual Linux serial nodes.
        if not re.fullmatch(r'/dev/(?:tty(?:USB|ACM|S|AMA)\d+|serial/by-id/[A-Za-z0-9_.:-]+)',port):
            raise ValueError('Use a mapped /dev/ttyUSB*, /dev/ttyACM* or /dev/serial/by-id/* port')
        baud=int(d.get('baudrate',19200 if kind=='epimax_serial' else 9600))
        if not 1200<=baud<=115200:raise ValueError('Invalid serial baud rate')
        cfg.update(port=port,baudrate=baud)
        if kind in {'epimax_serial','tdk_genesys','impac'}:
            cfg['address']=int(d.get('address',1 if kind=='epimax_serial' else 6 if kind=='tdk_genesys' else 0))
    if kind.startswith('epimax'):
        cfg['address']=int(d.get('address',1))
        cfg['units']=str(d.get('units','mBar'))
        if cfg['units'] not in ('mBar','Torr','Pa'):raise ValueError('Unsupported pressure unit')
        if not 1<=cfg['address']<=247:raise ValueError('Modbus address must be 1–247')
    if kind=='tdk_genesys' and not 0<=cfg['address']<=30: raise ValueError('TDK serial address must be 0–30')
    if kind=='impac' and not 0<=cfg['address']<=99:raise ValueError('IMPAC address must be 0–99')
    return cfg


def endpoint_key(cfg:dict) -> str:
    if cfg['type'] in TCP_TYPES:return f"tcp://{cfg['host']}:{cfg['tcp_port']}"
    return 'serial://'+cfg['port']


def make_driver(cfg:dict):
    kind=cfg['type']
    if kind in ('pfeiffer','ngc3'):
        from server.legacy_drivers import PfeifferReadOnly,NGC3ReadOnly
        cls=PfeifferReadOnly if kind=='pfeiffer' else NGC3ReadOnly
        return cls(cfg['port'],cfg['baudrate'])
    if kind=='agilent':
        from vacuum_extra_devices import Agilent4UHVDevice
        return Agilent4UHVDevice(cfg['port'],cfg['baudrate'])
    if kind=='epimax_serial':
        from vacuum_extra_devices import EpimaxPVCDevice
        return EpimaxPVCDevice(cfg['port'],cfg['baudrate'],address=cfg['address'],pressure_units=cfg['units'])
    if kind in TCP_TYPES:
        from epimax_network import EpimaxTCPDevice,make_endpoint
        mode='modbus_tcp' if kind=='epimax_modbus_tcp' else 'rtu_tcp_bridge'
        return EpimaxTCPDevice(make_endpoint(cfg['host'],cfg['tcp_port'],mode),
                               address=cfg['address'],pressure_units=cfg['units'])
    if kind in ('tdk_genesys','tdk_zplus'):
        from tdk_lambda import TDKLambdaDevice,GENESYS,ZPLUS
        return TDKLambdaDevice(cfg['port'],cfg['baudrate'],protocol=GENESYS if kind=='tdk_genesys' else ZPLUS,
                               address=cfg.get('address',6))
    if kind=='impac':
        from impac_pyrometer import Impac140
        return Impac140(cfg['port'],cfg['baudrate'],address=cfg.get('address',0))
    raise ValueError('Unsupported driver')


def to_metrics(cfg:dict,reading) -> tuple[dict,dict]:
    kind=cfg['type']; metrics={};diagnostics={}
    if kind in ('tdk_genesys','tdk_zplus'):
        if not reading.ok:raise ValueError('TDK did not return valid measured voltage/current')
        metrics={'voltage_V':{'value':reading.voltage_v,'unit':'V'},
                 'current_A':{'value':reading.current_a,'unit':'A'},
                 'power_W':{'value':reading.power_w,'unit':'W'}}
        diagnostics={'output':reading.output_state,'mode':reading.mode,'identity':reading.identity,
                     'set_voltage_V':reading.voltage_set_v,'set_current_A':reading.current_set_a}
    elif kind=='impac':
        val,raw=reading
        if val is None or not math.isfinite(val):raise ValueError('IMPAC temperature unavailable: '+str(raw))
        metrics={'temperature_C':{'value':val,'unit':'°C'}};diagnostics={'raw':str(raw)}
    else:
        if not getattr(reading,'ok',False):
            raise ValueError(str(getattr(reading,'parse_error','No valid pressure readings')))
        for name,gauge in reading.gauges.items():
            value=getattr(gauge,'pressure_mbar',None)
            if value is None or not math.isfinite(value) or value<=0:continue
            metrics[name]={'value':value,'unit':'mbar'}
        diagnostics=dict(getattr(reading,'diagnostics',{}))
        if getattr(reading,'parse_error',''):diagnostics['partial_error']=reading.parse_error
        if not metrics:raise ValueError('No valid pressure readings')
    return metrics,diagnostics


class Store:
    def __init__(self,path:Path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS systems (
                  slug TEXT PRIMARY KEY, name TEXT NOT NULL, layout TEXT NOT NULL DEFAULT '{}',
                  created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS devices (
                  id TEXT PRIMARY KEY, system_slug TEXT NOT NULL REFERENCES systems(slug) ON DELETE CASCADE,
                  config TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS readings (
                  device_id TEXT NOT NULL, system_slug TEXT NOT NULL, ts REAL NOT NULL,
                  metric TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_readings_system ON readings(system_slug,ts);
                CREATE INDEX IF NOT EXISTS idx_readings_device ON readings(device_id,ts);
                CREATE TABLE IF NOT EXISTS chamber_map (
                  system_slug TEXT PRIMARY KEY REFERENCES systems(slug) ON DELETE CASCADE,
                  mapping TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS hub_shortcuts (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS hub_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
    def connect(self):
        db=sqlite3.connect(str(self.path),timeout=8)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=8000')
        db.execute('PRAGMA journal_mode=WAL')
        return db
    def systems(self):
        with self.lock, self.connect() as db:
            return [{'slug':r['slug'],'name':r['name'],'layout':json.loads(r['layout'])}
                    for r in db.execute('SELECT * FROM systems ORDER BY created_at')]
    def devices(self,slug=None):
        with self.lock, self.connect() as db:
            rows=db.execute('SELECT * FROM devices '+('WHERE system_slug=? ' if slug else '')+'ORDER BY created_at',(slug,) if slug else ())
            return [dict({'id':r['id'],'system':r['system_slug']},**json.loads(r['config'])) for r in rows]
    def system(self,slug):
        return next((s for s in self.systems() if s['slug']==slug),None)
    def create_system(self,name,slug):
        name,slug=validate_system(name,slug)
        with self.lock, self.connect() as db:
            db.execute('INSERT INTO systems (slug,name,layout,created_at) VALUES (?,?,?,?)',
                       (slug,name,json.dumps({'columns':2,'order':[]}),time.time()))
        return slug
    def update_layout(self,slug,layout):
        if not isinstance(layout,dict):raise ValueError('Invalid layout')
        cols=int(layout.get('columns',2));order=layout.get('order',[])
        if cols not in (1,2,3) or not isinstance(order,list) or len(order)>200 or any(not isinstance(s,str) for s in order):
            raise ValueError('Invalid panel order / column count')
        ids={d['id'] for d in self.devices(slug)}
        if len(set(order))!=len(order) or any(x not in ids for x in order):raise ValueError('Unknown device ID in layout')
        with self.lock,self.connect() as db:
            if db.execute('UPDATE systems SET layout=? WHERE slug=?',(json.dumps({'columns':cols,'order':order}),slug)).rowcount==0:
                raise KeyError('System not found')
    def add_device(self,slug,d):
        cfg=validate_device(d)
        with self.lock,self.connect() as db:
            if not db.execute('SELECT 1 FROM systems WHERE slug=?',(slug,)).fetchone():raise KeyError('System not found')
            # No two workers can claim the same physical port / socket.
            keys={endpoint_key(json.loads(row[0])) for row in db.execute('SELECT config FROM devices')}
            if endpoint_key(cfg) in keys:raise ValueError('Instrument endpoint is already assigned to a system')
            device_id=uuid.uuid4().hex[:16]
            db.execute('INSERT INTO devices VALUES (?,?,?,?)',
                       (device_id,slug,json.dumps(cfg),time.time()))
        return device_id
    def delete_device(self,slug,device_id):
        with self.lock,self.connect() as db:
            if db.execute('DELETE FROM devices WHERE id=? AND system_slug=?',(device_id,slug)).rowcount!=1:
                raise KeyError('Instrument not found')
            # Retain previous recordings deliberately, even when a device is removed.
    def export_profile(self,slug):
        sys=self.system(slug)
        if not sys:raise KeyError('System not found')
        return {'format':'uhv-sentinel-server-profile','version':1,'name':sys['name'],
                'layout':sys['layout'],'devices':[{k:v for k,v in d.items() if k!='system'} for d in self.devices(slug)]}
    def import_profile(self,slug,data):
        if not isinstance(data,dict) or data.get('format')!='uhv-sentinel-server-profile' or data.get('version')!=1:
            raise ValueError('Invalid server profile file')
        configs=data.get('devices')
        if not isinstance(configs,list) or len(configs)>48:raise ValueError('Profile has too many instruments')
        devices=[(str(d.get('id') or uuid.uuid4().hex[:16]),validate_device(d)) for d in configs]
        ids=[x[0] for x in devices]
        if len(set(ids))!=len(ids) or any(not re.fullmatch(r'[A-Za-z0-9_-]{4,40}',id) for id in ids):
            raise ValueError('Invalid or repeated device IDs')
        layout=data.get('layout',{'columns':2,'order':[]})
        if not isinstance(layout,dict):raise ValueError('Invalid profile layout')
        cols=int(layout.get('columns',2));order=layout.get('order',[])
        if cols not in (1,2,3) or not isinstance(order,list) or any(i not in ids for i in order):
            raise ValueError('Invalid profile panel order')
        keys=[endpoint_key(x[1]) for x in devices]
        if len(set(keys))!=len(keys):raise ValueError('Repeated hardware ports in profile')
        with self.lock,self.connect() as db:
            if not db.execute('SELECT 1 FROM systems WHERE slug=?',(slug,)).fetchone():raise KeyError('System not found')
            occupied={endpoint_key(json.loads(row[0])) for row in db.execute('SELECT config FROM devices WHERE system_slug!=?',(slug,))}
            if any(k in occupied for k in keys):raise ValueError('An imported port is already used by another system')
            other_ids={r[0] for r in db.execute('SELECT id FROM devices WHERE system_slug!=?',(slug,))}
            id_mapping={ident:(uuid.uuid4().hex[:16] if ident in other_ids else ident) for ident,_ in devices}
            db.execute('DELETE FROM devices WHERE system_slug=?',(slug,))
            db.execute('DELETE FROM chamber_map WHERE system_slug=?',(slug,))
            now=time.time()
            for ident,cfg in devices:
                db.execute('INSERT INTO devices VALUES (?,?,?,?)',(id_mapping[ident],slug,json.dumps(cfg),now))
            order=[id_mapping[x] for x in order]
            db.execute('UPDATE systems SET layout=? WHERE slug=?',(json.dumps({'columns':cols,'order':order}),slug))
        # No electrical output states are included in profiles.
    def get_chambers(self,slug):
        with self.lock,self.connect() as db:
            row=db.execute('SELECT mapping FROM chamber_map WHERE system_slug=?',(slug,)).fetchone()
            return json.loads(row[0]) if row else {}
    def set_chambers(self,slug,mapping):
        if not isinstance(mapping,dict) or set(mapping)-{'p','a'}:raise ValueError('Invalid chamber mapping')
        devices={d['id'] for d in self.devices(slug)}
        normalized={}
        for key in ('p','a'):
            selection=mapping.get(key)
            if not selection:continue
            if not isinstance(selection,dict):raise ValueError('Invalid chamber selection')
            device_id=selection.get('device_id');metric=selection.get('metric')
            if device_id not in devices or not isinstance(metric,str) or not 1<=len(metric)<=70 or any(ord(c)<32 for c in metric):
                raise ValueError('Choose a pressure channel on an instrument in this system')
            normalized[key]={'device_id':device_id,'metric':metric}
        with self.lock,self.connect() as db:
            if not db.execute('SELECT 1 FROM systems WHERE slug=?',(slug,)).fetchone():raise KeyError('Unknown system')
            db.execute('INSERT OR REPLACE INTO chamber_map VALUES (?,?)',(slug,json.dumps(normalized)))
        return normalized
    def shortcuts(self):
        with self.lock,self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,title,url FROM hub_shortcuts ORDER BY created_at')]
    def add_shortcut(self,title,url):
        title=str(title).strip();url=str(url).strip()
        if not 1<=len(title)<=60 or not 1<=len(url)<=500:raise ValueError('Invalid shortcut name or URL')
        if url.startswith('/s/'):
            if not SLUG.fullmatch(url[3:]) or not self.system(url[3:]):
                raise ValueError('Internal shortcut must link to an existing system')
        else:
            parsed=urlsplit(url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or any(ord(c)<32 for c in url) or parsed.fragment:
                raise ValueError('Use a complete http(s) URL without credentials or fragments')
            # Link only. The server will never fetch external URLs.
        identifier=uuid.uuid4().hex[:12]
        with self.lock,self.connect() as db:
            db.execute('INSERT INTO hub_shortcuts VALUES (?,?,?,?)',(identifier,title,url,time.time()))
        return identifier
    def delete_shortcut(self,identifier):
        with self.lock,self.connect() as db:
            if db.execute('DELETE FROM hub_shortcuts WHERE id=?',(identifier,)).rowcount!=1:
                raise KeyError('Shortcut not found')
    def get_hub_title(self):
        with self.lock,self.connect() as db:
            row=db.execute("SELECT value FROM hub_settings WHERE key='title'").fetchone()
        return row[0] if row else 'UHV Sentinel Hub'
    def set_hub_title(self,title):
        title=str(title).strip()
        if not 1<=len(title)<=72 or any(ord(c)<32 for c in title):raise ValueError('Invalid hub title')
        with self.lock,self.connect() as db:
            db.execute("INSERT OR REPLACE INTO hub_settings VALUES ('title',?)",(title,))

    def record(self,slug,device_id,ts,metrics):
        with self.lock,self.connect() as db:
            db.executemany('INSERT INTO readings VALUES (?,?,?,?,?,?)',
                           [(device_id,slug,ts,name,float(v['value']),v['unit']) for name,v in metrics.items()
                            if math.isfinite(float(v['value']))])
    def history(self,slug,hours=24,limit=1500):
        hours=max(0.05,min(24*365,float(hours)));limit=max(1,min(5000,int(limit)))
        with self.lock,self.connect() as db:
            rows=db.execute('SELECT device_id,ts,metric,value,unit FROM '
                            '(SELECT device_id,ts,metric,value,unit FROM readings '
                            'WHERE system_slug=? AND ts>=? ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC',
                            (slug,time.time()-hours*3600,limit))
            return [dict(row) for row in rows]
    def cleanup(self,days):
        with self.lock,self.connect() as db:
            db.execute('DELETE FROM readings WHERE ts<?',(time.time()-int(days)*86400,))


class Poller:
    def __init__(self,store:Store,retention_days=365,make=make_driver):
        self.store=store;self.retention_days=retention_days;self.make=make
        self.stop_event=threading.Event();self.lock=threading.RLock()
        self.workers={};self.live={};self.scheduler=None
    def start(self):
        if self.scheduler and self.scheduler.is_alive():return
        self.scheduler=threading.Thread(target=self._run,daemon=True,name='uhv-manager');self.scheduler.start()
    def stop(self):
        self.stop_event.set()
        if self.scheduler:self.scheduler.join(timeout=4)
        with self.lock:workers=list(self.workers.values())
        for item in workers:item[0].set()
        for item in workers:item[1].join(timeout=4)
    def _run(self):
        last_cleanup=0
        while not self.stop_event.is_set():
            desired={d['id']:d for d in self.store.devices() if d['enabled']}
            with self.lock:
                for ident,(event,thread) in list(self.workers.items()):
                    if ident not in desired or not thread.is_alive():
                        event.set()
                        if not thread.is_alive():self.workers.pop(ident,None)
                for ident,device in desired.items():
                    if ident not in self.workers:
                        ev=threading.Event();th=threading.Thread(target=self._worker,args=(ev,device),daemon=True,name='uhv-'+ident)
                        self.workers[ident]=(ev,th);th.start()
            if time.time()-last_cleanup>86400:
                self.store.cleanup(self.retention_days);last_cleanup=time.time()
            self.stop_event.wait(1)
    def reconfigure(self):
        with self.lock:
            for ev,_ in self.workers.values():ev.set()
        # scheduler will start replacements when stopped workers exit
    def snapshot(self,slug):
        with self.lock:return {d['id']:self.live.get(d['id'],{'state':'pending','metrics':{},'last_success':None})
                              for d in self.store.devices(slug)}
    def _worker(self,ev,d):
        ident=d['id'];driver=None;failures=0
        try:
            while not ev.is_set() and not self.stop_event.is_set():
                started=time.monotonic()
                try:
                    if driver is None:
                        driver=self.make(d);driver.open()
                    if d['type']=='impac':res=driver.read_temperature()
                    else:res=driver.read_status()
                    metrics,diagnostics=to_metrics(d,res)
                    self.store.record(d['system'],ident,time.time(),metrics)
                    with self.lock:self.live[ident]={'state':'ok','metrics':metrics,'diagnostics':diagnostics,
                                                     'last_success':time.time(),'error':None,'failures':0}
                    failures=0
                except Exception as exc:
                    failures+=1
                    with self.lock:
                        previous=self.live.get(ident,{})
                        self.live[ident]={'state':'stale' if previous.get('last_success') else 'error',
                                         'metrics':{},'diagnostics':{},'last_success':previous.get('last_success'),
                                         'error':str(exc)[:350],'failures':failures}
                    if failures>=2 and driver is not None:
                        try:driver.close()
                        except Exception:pass
                        driver=None
                ev.wait(max(.15,float(d['interval'])-(time.monotonic()-started)))
        finally:
            if driver is not None:
                try:driver.close()
                except Exception:pass
            with self.lock:self.workers.pop(ident,None)
