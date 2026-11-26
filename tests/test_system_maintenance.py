"""OS maintenance integration: command isolation, status, web auth and reboot safety."""
import http.client
import json
import tempfile
import threading
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server.core import Store, Poller
from server.web import Auth, Server
from server.hub import get_maintenance_status, queue_maintenance, queue_reboot
if sys.platform.startswith('linux'):
    from scripts import system_maintenance, host_tasks
else:
    system_maintenance = host_tasks = None


@unittest.skipUnless(sys.platform.startswith('linux'), 'Ubuntu host-only worker')
class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.records = self.root / 'server-data' / 'records'
        self.records.mkdir(parents=True)
        self.logs = self.root / 'logs'
        for target, attr, value in (
            (system_maintenance, 'ROOT', self.root),
            (system_maintenance, 'DATA', self.records),
            (system_maintenance, 'LOG_DIR', self.logs),
            (system_maintenance, 'LOCK', self.root / 'root-ops.lock'),
            (host_tasks, 'ROOT', self.root),
            (host_tasks, 'DATA', self.records),
        ):
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def test_request_schema_rejects_arbitrary_commands_and_old_requests(self):
        queue_maintenance(self.records, 'check')
        req = json.loads((self.records / 'maintenance-request.json').read_text())
        self.assertEqual(set(req), {'kind', 'requested_at'})
        self.assertEqual(req['kind'], 'check')
        with self.assertRaises(ValueError): queue_maintenance(self.records, 'install')
        with self.assertRaises(ValueError): system_maintenance._checked_request(
            self.records / 'maintenance-request.json', {'install'})
        (self.records / 'maintenance-request.json').write_text(
            json.dumps({'kind': 'check', 'requested_at': time.time(), 'command': 'rm -rf /'}))
        with self.assertRaises(ValueError): system_maintenance._checked_request(
            self.records / 'maintenance-request.json', {'check'})
        (self.records / 'maintenance-request.json').write_text(
            json.dumps({'kind': 'check', 'requested_at': time.time() - 99999}))
        with self.assertRaises(ValueError): system_maintenance._checked_request(
            self.records / 'maintenance-request.json', {'check'})

    def fake_run(self, calls):
        def do_run(self, *args, **kwargs):
            calls.append(args)
            if args[:2] == ('apt', 'list'):
                return ('Listing... Done\npython3/ubuntu 3.13 all [upgradable from: 3.12]\n'
                        'docker.io/ubuntu 28 all [upgradable from: 27]\n')
            if args[:2] == ('apt-get', '-s'):
                return ('Inst docker.io [27] (28 Ubuntu:26.04 [amd64])\n'
                        'Inst python3 [3.12] (3.13 Ubuntu:26.04 [amd64])\n')
            if args == ('docker', '--version'): return 'Docker version 28.0\n'
            if args == ('docker', 'compose', 'version'): return 'Docker Compose version v2.30\n'
            if args[-4:] == ('ps', '-q', 'monitor'): return 'mock-monitor-id\n'
            return 'Done\n'
        return do_run

    def test_check_then_install_fixed_apt_and_docker_commands(self):
        commands = []
        with patch.object(system_maintenance.Maintenance, 'run', self.fake_run(commands)):
            queue_maintenance(self.records, 'check')
            system_maintenance.process()
            status = get_maintenance_status(self.records)
            self.assertEqual(status['state'], 'ready')
            self.assertEqual(status['packages_count'], 2)
            self.assertTrue(status['can_install'])
            self.assertFalse(status['pending'])
            queue_maintenance(self.records, 'install')
            system_maintenance.process()
        status = get_maintenance_status(self.records)
        self.assertEqual(status['state'], 'completed')
        self.assertEqual(status['progress'], 100)
        self.assertTrue(status['can_reboot'])
        self.assertGreater(len(status['logs']), 0)
        self.assertTrue(any(c[0] == 'apt-get' and 'full-upgrade' in c for c in commands))
        self.assertTrue(any(c[:2] == ('docker', 'compose') and 'pull' in c for c in commands))
        self.assertTrue(any(c[:2] == ('docker', 'compose') and 'build' in c for c in commands))
        self.assertFalse(any(c[0] in ('sh', 'bash', 'sudo') for c in commands))

    def test_package_removals_block_automatic_upgrade(self):
        commands = []
        def fake_run(obj, *args, **kwargs):
            commands.append(args)
            if args[:2] == ('apt', 'list'): return 'Listing... Done\nx/ubuntu 2 all [upgradable from 1]\n'
            if args[:2] == ('apt-get', '-s'): return 'Remv docker.io [27]\nInst x [1] (2 Ubuntu [all])\n'
            if args == ('docker', '--version'): return 'Docker version 27'
            if args == ('docker', 'compose', 'version'): return 'Docker Compose version 2'
            return 'Done'
        with patch.object(system_maintenance.Maintenance, 'run', fake_run):
            queue_maintenance(self.records, 'check')
            system_maintenance.process()
            status = get_maintenance_status(self.records)
            self.assertFalse(status['can_install'])
            self.assertEqual(status['removals'], ['docker.io'])
            with self.assertRaises(ValueError): queue_maintenance(self.records, 'install')
            self.assertFalse(any('full-upgrade' in c and '-y' in c for c in commands))

    def test_reboot_not_allowed_before_success_and_confirms_after(self):
        with self.assertRaises(ValueError): queue_reboot(self.records)
        host_tasks._write('maintenance-status.json',
                          {'state': 'completed', 'can_reboot': True, 'progress':100, 'logs':['done']})
        queue_reboot(self.records)
        self.assertTrue((self.records/'maintenance-reboot.json').exists())
        with patch.object(system_maintenance.subprocess, 'run') as fake,\
             patch.object(system_maintenance, '_boot_id', return_value='old-boot'):
            system_maintenance.reboot()
        fake.assert_called_once()
        self.assertEqual(fake.call_args.args[0], ['systemctl', 'reboot'])
        self.assertFalse((self.records/'maintenance-reboot.json').exists())
        self.assertEqual(get_maintenance_status(self.records)['state'], 'rebooting')
        host_tasks._write('host-stats.json',
                          {'updated_at':time.time(), 'boot_id':'new-boot', 'hostname':'test'})
        self.assertEqual(get_maintenance_status(self.records)['state'], 'restarted')

    def test_new_web_api_requires_login_and_csrf(self):
        store = Store(self.records / 'uhv-server.sqlite3')
        poller = Poller(store)
        auth = Auth(b'correct very long secret password', b'A' * 64)
        server = Server(('127.0.0.1', 0), store, poller, auth)
        task = threading.Thread(target=server.serve_forever, daemon=True)
        task.start()
        conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
        try:
            conn.request('GET', '/api/hub/maintenance')
            r = conn.getresponse();self.assertEqual(r.status, 401);r.read()
            conn.request('POST', '/api/login',
                         json.dumps({'password':'correct very long secret password'}),
                         {'Content-Type':'application/json'})
            r=conn.getresponse();self.assertEqual(r.status, 200)
            cookie=r.getheader('Set-Cookie').split(';')[0]
            csrf=json.loads(r.read())['csrf']
            head={'Cookie':cookie,'Content-Type':'application/json','X-UHV-CSRF':csrf}
            conn.request('POST', '/api/hub/maintenance/check', '{}',
                         {'Cookie':cookie,'Content-Type':'application/json'})
            r=conn.getresponse();self.assertEqual(r.status, 403);r.read()
            conn.request('POST','/api/hub/maintenance/check',
                         json.dumps({'shell':'rm -rf /'}),head)
            r=conn.getresponse();self.assertEqual(r.status, 202);r.read()
            req=json.loads((self.records/'maintenance-request.json').read_text())
            self.assertEqual(set(req), {'kind', 'requested_at'})
            self.assertEqual(req['kind'], 'check')
            conn.request('GET','/api/hub/maintenance',headers={'Cookie':cookie})
            r=conn.getresponse();self.assertEqual(r.status, 200);self.assertTrue(json.loads(r.read())['pending'])
            conn.request('POST', '/api/hub/maintenance/reboot', '{}', head)
            r=conn.getresponse();self.assertEqual(r.status, 400);r.read()
        finally:
            conn.close();server.shutdown();server.server_close();task.join(3)

    def test_parser_and_root_script_never_executes_unsafe_payload(self):
        self.assertEqual(system_maintenance._apt_packages(
            'Listing... Done\nlibc6:amd64/noble 2 amd64 [upgradable from: 1]\n'), ['libc6:amd64'])
        self.assertEqual(system_maintenance._upgrade_plan('Remv systemd [1]\nInst docker.io [1]\n'),
                         ['systemd'])

if __name__ == '__main__':unittest.main()
