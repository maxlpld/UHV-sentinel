"""Headless server regression checks, using simulated instruments only."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from server.core import Store,Poller,validate_device,endpoint_key,to_metrics
from server.web import Auth,Server

class DummyReading:
    ok=True
    voltage_v=4.5
    current_a=1.2
    power_w=5.4
    output_state='OFF'
    mode='SCPI'
    identity='TDK'
    voltage_set_v=4.5
    current_set_a=1.5

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'history.sqlite3')
        self.store.create_system('Downstairs A','downstairs-a')
        self.store.create_system('Downstairs B','downstairs-b')
    def tearDown(self):self.tmp.cleanup()
    def test_isolated_systems_and_history(self):
        did=self.store.add_device('downstairs-a',{'name':'Pfeiffer','type':'pfeiffer','port':'/dev/ttyUSB0'})
        self.store.record('downstairs-a',did,time.time(),{'Channel 1':{'value':1e-8,'unit':'mbar'}})
        self.assertEqual(len(self.store.history('downstairs-a')),1)
        self.assertEqual(self.store.history('downstairs-b'),[])
        self.assertEqual(self.store.devices('downstairs-b'),[])
    def test_duplicate_port_rejected(self):
        self.store.add_device('downstairs-a',{'name':'D1','type':'agilent','port':'/dev/ttyUSB0'})
        with self.assertRaises(ValueError):
            self.store.add_device('downstairs-b',{'name':'D2','type':'pfeiffer','port':'/dev/ttyUSB0'})
    def test_profile_preserves_settings_not_outputs(self):
        did=self.store.add_device('downstairs-a',{'name':'TDK','type':'tdk_genesys','port':'/dev/ttyUSB5'})
        self.store.update_layout('downstairs-a',{'columns':3,'order':[did]})
        exported=self.store.export_profile('downstairs-a')
        self.assertNotIn('set_voltage',json.dumps(exported))
        self.store.import_profile('downstairs-a',exported)
        self.assertEqual(self.store.system('downstairs-a')['layout']['columns'],3)
        self.assertEqual(self.store.devices('downstairs-a')[0]['name'],'TDK')
    def test_invalid_serial_paths_and_tcp_ports(self):
        for bad in ('/etc/passwd','COM4','/dev/ttyUSB0;rm -rf /','/dev/dri/card0'):
            with self.assertRaises(ValueError):validate_device({'type':'agilent','name':'A','port':bad})
        with self.assertRaises(ValueError):validate_device({'type':'epimax_modbus_tcp','name':'E','host':'10.0.0.1','tcp_port':70000})
    def test_dummy_tdk_metrics_are_read_only(self):
        metrics,d=to_metrics({'type':'tdk_zplus'},DummyReading())
        self.assertEqual(metrics['current_A']['value'],1.2)
        self.assertEqual(metrics['power_W']['value'],5.4)
        self.assertNotIn('enable_output',d)
    def test_poller_isolates_failing_devices(self):
        self.store.add_device('downstairs-a',{'type':'agilent','name':'A','port':'/dev/ttyUSB0','interval':2})
        self.store.add_device('downstairs-b',{'type':'tdk_zplus','name':'B','port':'/dev/ttyUSB1','interval':2})
        def factory(cfg):
            d=Mock()
            if cfg['type']=='agilent':d.read_status.side_effect=OSError('no COM device')
            else:d.read_status.return_value=DummyReading()
            return d
        poller=Poller(self.store,make=factory);poller.start()
        try:
            for _ in range(50):
                if self.store.history('downstairs-b'):break
                time.sleep(.03)
            self.assertTrue(self.store.history('downstairs-b'))
            self.assertEqual(self.store.history('downstairs-a'),[])
            self.assertNotEqual(poller.snapshot('downstairs-a'),{})
        finally:poller.stop()

    def test_authenticated_api_two_links_and_no_control_endpoints(self):
        import http.client
        import threading
        import urllib.parse
        auth=Auth(b'this is a sufficiently long test password',b'R'*48)
        poller=Poller(self.store,make=lambda d: Mock())
        app=Server(('127.0.0.1',0),self.store,poller,auth)
        thread=threading.Thread(target=app.serve_forever,daemon=True)
        thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',app.server_address[1],timeout=4)
        try:
            connection.request('GET','/api/systems')
            response=connection.getresponse();self.assertEqual(response.status,401);response.read()
            body=json.dumps({'password':'this is a sufficiently long test password'})
            connection.request('POST','/api/login',body,{'Content-Type':'application/json'})
            response=connection.getresponse();self.assertEqual(response.status,200)
            cookie=response.getheader('Set-Cookie').split(';')[0]
            session=json.loads(response.read());csrf=session['csrf']
            headers={'Cookie':cookie,'X-UHV-CSRF':csrf,'Content-Type':'application/json'}
            connection.request('GET','/api/systems',headers=headers)
            systems=json.loads(connection.getresponse().read())
            self.assertEqual({s['slug'] for s in systems},{'downstairs-a','downstairs-b'})
            connection.request('GET','/s/downstairs-a',headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,200);response.read()
            connection.request('POST','/api/systems/downstairs-a/tdk/enable', '{}',headers)
            response=connection.getresponse();self.assertEqual(response.status,404);response.read()
            connection.request('POST','/api/systems/downstairs-a/layout',json.dumps({'columns':3,'order':[]}),headers)
            response=connection.getresponse();self.assertEqual(response.status,200);response.read()
        finally:
            connection.close();app.shutdown();app.server_close();thread.join(timeout=3)

    def test_auth_signatures_and_expiry(self):
        auth=Auth(b'a strong random laboratory password',b'Z'*48)
        self.assertFalse(auth.verify('wrong','127.0.0.1'))
        self.assertTrue(auth.verify('a strong random laboratory password','127.0.0.1'))
        t=auth.make();self.assertTrue(auth.check(t))
        self.assertFalse(auth.check(t+'evil'))
        self.assertEqual(len(auth.csrf(t)),64)

if __name__=='__main__':unittest.main()
