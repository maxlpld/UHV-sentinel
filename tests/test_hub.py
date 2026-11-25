"""Read-only hub integration and isolated multi-lab summary tests."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from server.core import Store,Poller
from server.hub import summarize_systems,host_telemetry,get_release_status,queue_update,version_tuple
from server.web import Auth,Server

class HubTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'records'/'uhv-server.sqlite3')
        self.store.create_system('RT-A','rt-a');self.store.create_system('RT-B','rt-b')
        self.a=self.store.add_device('rt-a',{'name':'Gauge A','type':'pfeiffer','port':'/dev/ttyUSB0'})
        self.b=self.store.add_device('rt-b',{'name':'Gauge B','type':'pfeiffer','port':'/dev/ttyUSB1'})
        self.poller=Poller(self.store)
    def test_explicit_pressure_mapping_no_guessed_readings(self):
        self.poller.live[self.a]={'state':'ok','metrics':{'P1':{'value':1.4e-9,'unit':'mbar'},'P2':{'value':2e-9,'unit':'mbar'}},'last_success':time.time()}
        systems=summarize_systems(self.store,self.poller)
        self.assertFalse(systems[0]['chambers']['p']['fresh'])
        self.store.set_chambers('rt-a',{'p':{'device_id':self.a,'metric':'P1'},'a':{'device_id':self.a,'metric':'P2'}})
        systems=summarize_systems(self.store,self.poller)
        self.assertEqual(systems[0]['chambers']['p']['value'],1.4e-9)
        self.assertEqual(systems[0]['chambers']['a']['value'],2e-9)
        self.assertFalse(systems[1]['chambers']['p']['fresh'])
        self.poller.live[self.a]['state']='stale'
        self.assertIsNone(summarize_systems(self.store,self.poller)[0]['chambers']['p']['value'])
        with self.assertRaises(ValueError):self.store.set_chambers('rt-b',{'p':{'device_id':self.a,'metric':'P1'}})
    def test_profile_restores_home_pressure_mappings(self):
        self.store.set_chambers('rt-a',{'p':{'device_id':self.a,'metric':'P1'}})
        profile=self.store.export_profile('rt-a')
        self.assertEqual(profile['chambers']['p']['metric'],'P1')
        self.store.import_profile('rt-a',profile)
        mapping=self.store.get_chambers('rt-a')
        self.assertEqual(mapping['p']['device_id'],self.store.devices('rt-a')[0]['id'])
        self.assertEqual(mapping['p']['metric'],'P1')

    def test_custom_shortcuts_validation_and_persistence(self):
        key=self.store.add_shortcut('RT-C','/s/rt-b')
        self.store.add_shortcut('Lab tool','https://192.168.0.15:9443/')
        self.assertEqual(len(self.store.shortcuts()),2)
        self.store.delete_shortcut(key);self.assertEqual(len(self.store.shortcuts()),1)
        for link in ('javascript:alert(1)','file:///etc/passwd','https://me:secret@server/','/s/nope','https://internal/#fragment'):
            with self.assertRaises(ValueError):self.store.add_shortcut('bad',link)
        self.store.set_hub_title('Downstairs laboratory')
        self.assertEqual(self.store.get_hub_title(),'Downstairs laboratory')
        self.assertEqual(Store(self.store.path).get_hub_title(),'Downstairs laboratory')
    def test_host_metrics_staleness_and_no_fake_cpu_temp(self):
        root=self.store.path.parent
        self.assertFalse(host_telemetry(root)['available'])
        (root/'host-stats.json').write_text(json.dumps({'updated_at':time.time(),'cpu_temp_c':None,'hostname':'test-server'}))
        data=host_telemetry(root);self.assertTrue(data['available']);self.assertIsNone(data['cpu_temp_c'])
        (root/'host-stats.json').write_text(json.dumps({'updated_at':time.time()-1000}))
        self.assertFalse(host_telemetry(root)['available'])
    def test_update_queue_never_executes_arbitrary_browser_command(self):
        root=self.store.path.parent
        queue_update(root)
        obj=json.loads((root/'update-request.json').read_text())
        self.assertEqual(set(obj),{'kind','requested_at'})
        with self.assertRaises(ValueError):queue_update(root)
        self.assertEqual(version_tuple('v0.0.7'),(0,0,7))
        self.assertIsNone(version_tuple('main'))
    def test_latest_release_check_is_read_only(self):
        from server import hub
        class MockResponse:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return b'{"tag_name": "v0.0.7"}'
        with patch('server.hub.urllib.request.urlopen', return_value=MockResponse()):
            hub._cache['at']=0
            result=get_release_status(self.store.path.parent)
        self.assertEqual(result['latest'],'v0.0.7')
        self.assertTrue(result['available'])
        self.assertFalse((self.store.path.parent/'update-request.json').exists())

    @unittest.skipUnless(__import__('sys').platform.startswith('linux'),'Ubuntu host only')
    def test_host_update_uses_fixed_github_source_not_request_commands(self):
        from scripts import host_tasks
        from subprocess import CompletedProcess
        root=Path(self.tmp.name)/'repo';root.mkdir()
        data=self.store.path.parent
        (data/'update-request.json').write_text(json.dumps({'kind':'install-latest-release',
                                    'requested_at':time.time(),'command':'malicious'}))
        calls=[]
        def fake_run(*args,**kwargs):
            calls.append(args)
            if 'rev-parse' in args:
                return CompletedProcess(args,0,stdout='oldsha\n' if args[-1]=='HEAD' else 'newsha\n')
            return CompletedProcess(args,0,stdout='ok')
        with patch.object(host_tasks,'DATA',data),patch.object(host_tasks,'ROOT',root),             patch.object(host_tasks,'_latest',return_value='v0.0.6'),             patch.object(host_tasks,'_run',side_effect=fake_run):
            host_tasks.update()
        self.assertFalse((data/'update-request.json').exists())
        self.assertEqual(json.loads((data/'update-status.json').read_text())['state'],'success')
        self.assertFalse(any('malicious' in str(x) for x in calls))

    def test_hub_api_login_csrf_and_system_isolation(self):
        auth=Auth(b'this is a very strong local test password',b'Z'*48)
        server=Server(('127.0.0.1',0),self.store,self.poller,auth)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=4)
        try:
            conn.request('GET','/api/hub');response=conn.getresponse();self.assertEqual(response.status,401);response.read()
            payload=json.dumps({'password':'this is a very strong local test password'})
            conn.request('POST','/api/login',payload,{'Content-Type':'application/json'})
            response=conn.getresponse();token=response.getheader('Set-Cookie').split(';')[0];csrf=json.loads(response.read())['csrf']
            headers={'Cookie':token,'Content-Type':'application/json','X-UHV-CSRF':csrf}
            conn.request('GET','/api/hub',headers=headers)
            response=conn.getresponse();self.assertEqual(response.status,200)
            data=json.loads(response.read())
            self.assertEqual({x['slug'] for x in data['systems']},{'rt-a','rt-b'})
            conn.request('POST','/api/hub/shortcuts',json.dumps({'title':'Pumps','url':'/s/rt-a'}),{'Cookie':token,'Content-Type':'application/json'})
            response=conn.getresponse();self.assertEqual(response.status,403);response.read()
            conn.request('POST','/api/hub/shortcuts',json.dumps({'title':'Pumps','url':'/s/rt-a'}),headers)
            response=conn.getresponse();self.assertEqual(response.status,201);response.read()
            conn.request('GET','/settings',headers=headers)
            response=conn.getresponse();self.assertEqual(response.status,200);self.assertIn(b'Hub settings',response.read())
            conn.request('POST','/api/systems/rt-a/tdk/enable','{}',headers)
            response=conn.getresponse();self.assertEqual(response.status,404);response.read()
        finally:
            conn.close();server.shutdown();server.server_close();thread.join(timeout=3)

if __name__=='__main__':unittest.main()
