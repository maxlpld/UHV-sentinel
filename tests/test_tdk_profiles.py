"""Offline TDK serial protocol, persistent V/I log, and portable-profile tests."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import profiles
from tdk_lambda import TDKLambdaDevice, GENESYS, ZPLUS, parse_number, validate_setpoint
from tdk_logging import TDKLogger


class FakeTDKSerial:
    created = []
    def __init__(self, *args, **kwargs):
        self.sent = []
        self.buf = bytearray()
        self.voltage_setting = 3.0
        self.current_setting = 2.0
        self.output = False
        self.is_open = True
        FakeTDKSerial.created.append(self)

    def reset_input_buffer(self):
        self.buf.clear()
    def flush(self): pass
    def close(self): self.is_open = False
    def write(self, bytes_):
        self.sent.append(bytes_)
        cmd = bytes_.decode('ascii').strip()
        genesys = bytes_.endswith(b'\r')
        replies = {
            'ADR 6': 'OK', 'IDN?': 'LAMBDA, GEN12-25',
            '*IDN?': 'TDK-Lambda,Z+20-10,TEST,1.0',
            'MV?': '1.800', 'MC?': '1.850', 'PV?': str(self.voltage_setting),
            'PC?': str(self.current_setting), 'MODE?': 'CV' if self.output else 'OFF',
            'MEAS:VOLT?': '1.800', 'MEAS:CURR?': '1.850',
            'VOLT?': str(self.voltage_setting), 'CURR?': str(self.current_setting),
            'OUTP?': '1' if self.output else '0',
        }
        if cmd.startswith(('PV ', 'VOLT ')):
            self.voltage_setting = float(cmd.split()[1])
        elif cmd.startswith(('PC ', 'CURR ')):
            self.current_setting = float(cmd.split()[1])
        elif cmd.startswith(('OUT ', 'OUTP ')):
            self.output = bool(int(cmd.split()[1]))
        reply = replies.get(cmd)
        if reply is None and genesys:
            reply = 'OK'
        if reply is not None:
            self.buf.extend(reply.encode() + (b'\r' if genesys else b'\n'))
        return len(bytes_)
    def read_until(self, terminator=b'\n', size=256):
        if not self.buf:
            return b''
        idx=self.buf.find(terminator)
        take=min(size,idx+1 if idx>=0 else len(self.buf))
        b=bytes(self.buf[:take])
        del self.buf[:take]
        return b


class TDKTests(unittest.TestCase):
    def test_queries_are_read_only_genesys(self):
        with patch('tdk_lambda.serial.Serial', FakeTDKSerial):
            device=TDKLambdaDevice('COM1',9600,protocol=GENESYS,address=6)
            device.open()
            status=device.read_status()
            self.assertEqual(status.output_state,'OFF')
            self.assertAlmostEqual(status.current_a,1.85)
            self.assertAlmostEqual(status.power_w,3.33)
            writes=device.ser.sent
            self.assertTrue(all(w.decode().strip().endswith('?') or w==b'ADR 6\r' for w in writes))
            device.close()

    def test_zplus_scpi_and_explicit_output(self):
        with patch('tdk_lambda.serial.Serial', FakeTDKSerial):
            device=TDKLambdaDevice('COM2',protocol=ZPLUS)
            device.open()
            s=device.read_status()
            self.assertAlmostEqual(s.voltage_set_v,3.0)
            self.assertEqual(s.output_state,'OFF')
            with self.assertRaises(ValueError):
                device.set_voltage(10,5)
            device.set_voltage(2,5)
            device.set_current(1.85,2)
            device.set_output(True)
            self.assertEqual(device.read_status().output_state,'ON')
            self.assertTrue(any(w==b'OUTP 1\n' for w in device.ser.sent))
            device.close()

    def test_reject_bad_measurements(self):
        for bad in ('NaN','inf','E03','','1 V','1,25'):
            with self.assertRaises(ValueError):parse_number(bad)
        for bad in (float('nan'),-0.1,1.9):
            with self.assertRaises(ValueError):validate_setpoint(bad,1.85,'A')

    def test_tdk_persistent_logger_and_csv(self):
        from tdk_lambda import TDKReading
        with tempfile.TemporaryDirectory() as d:
            log=TDKLogger(Path(d))
            r=TDKReading(time.time(),1.5,1.85,2,2,'ON','CC','Fake')
            log.append('COM3','Manipulator',r)
            self.assertEqual(len(log.rows('COM3')),1)
            self.assertEqual(len(log.rows('OTHER')),0)
            path=Path(d)/'out.csv'
            self.assertEqual(log.export_csv('COM3',path),1)
            self.assertIn('voltage_v,current_a,power_w',path.read_text())
            self.assertIn('1.85',path.read_text())

    def test_profile_is_safe_portable_and_versioned(self):
        raw={'controllers':[{'port':'COM3','device_type':'TDK-Lambda power supply',
            'name':'Manipulator','baudrate':9600,'state':{'protocol':GENESYS,'address':6,
            'voltage_limit_v':'5','current_limit_a':'2','armed':True,'output_state':'ON',
            'set_current':'1.85','graph_range':'1 h'}},
            {'port':'COM4','name':'IG','device_type':'AML NGC3','baudrate':9600,
              'state':{'software_auto':True,'last_emission_request':'5mA','graph_range':'24 h'}}],
             'alert_settings':{'dark_mode':True,'smtp_password':'secret','remote_control_enabled':True},
             'layout':{'columns':3,'window_geometry':'1300x760+200+200'}}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'testing.json'
            profiles.save_profile('Testing',raw,dest=path)
            data=profiles.load_profile(path)
            self.assertEqual(data['config']['layout']['columns'],3)
            self.assertEqual(data['config']['controllers'][0]['state']['protocol'],GENESYS)
            self.assertFalse(data['config']['alert_settings']['remote_control_enabled'])
            self.assertNotIn('smtp_password',path.read_text())
            self.assertNotIn('armed',path.read_text())
            self.assertNotIn('software_auto',path.read_text())
            self.assertNotIn('output_state',path.read_text())
            with self.assertRaises(ValueError):
                profiles.validate_profile({'format':'unknown','schema_version':1,'config':{}})

    def test_embedded_theme_survives_export(self):
        with tempfile.TemporaryDirectory() as d:
            theme=Path(d)/'My_theme.json'
            theme.write_text('{"light":{"background":"#aabbcc"}}',encoding='utf-8')
            saved=Path(d)/'profile.json'
            profiles.save_profile('Lab 1',{'controllers':[]},dest=saved,theme_path=theme)
            loaded=profiles.load_profile(saved)
            self.assertIn('theme_asset',loaded)
            other=Path(d)/'export.json'
            profiles.save_profile_payload(loaded,other)
            self.assertEqual(profiles.load_profile(other)['theme_asset'],loaded['theme_asset'])
            with patch('profiles.data_dir',return_value=Path(d)):
                name=profiles.install_embedded_theme(loaded)
                self.assertEqual(json.loads((Path(d)/'themes'/name).read_text()),{'light':{'background':'#aabbcc'}})


if __name__=='__main__':unittest.main()
