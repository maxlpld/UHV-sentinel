"""All tests are offline; do not touch real user installations."""
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import installer
import updater
from version import __version__


def fake_release_bytes(version='0.2.1', bad_path=None):
    required = {name: b'\n' for name in updater._REQUIRED}
    required['version.py'] = f'__version__ = "{version}"\n'.encode()
    required['release_config.json'] = json.dumps(updater.release_settings()).encode()
    if bad_path:
        required[bad_path] = b'untrusted code'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zip_file:
        for name, data in required.items():
            zip_file.writestr(name, data)
    return buffer.getvalue()


class UpdateTests(unittest.TestCase):
    def test_versions(self):
        self.assertLess(updater.version_tuple('0.1.0'), updater.version_tuple('v0.2.1'))
        with self.assertRaises(ValueError):
            updater.version_tuple('0.1.0-rc1')
        with self.assertRaises(ValueError):
            updater.version_tuple('0.1')

    def test_sha256_strict(self):
        digest = 'f' * 64
        self.assertEqual(updater.checksum_for(digest + '  uhv-sentinel-release.zip',
                                              'uhv-sentinel-release.zip'), digest)
        with self.assertRaises(ValueError):
            updater.checksum_for('', 'uhv-sentinel-release.zip')
        with self.assertRaises(ValueError):
            updater.checksum_for((digest + '  uhv-sentinel-release.zip\n') * 2,
                                 'uhv-sentinel-release.zip')

    def test_valid_release_and_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / 'release.zip'
            package.write_bytes(fake_release_bytes())
            updater.validate_archive(package, '0.2.1')
            with self.assertRaisesRegex(ValueError, 'version'):
                updater.validate_archive(package, '0.2.2')

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / 'release.zip'
            package.write_bytes(fake_release_bytes(bad_path='../escape.py'))
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                updater.validate_archive(package, '0.2.1')

    def test_missing_required_files_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / 'release.zip'
            with zipfile.ZipFile(package, 'w') as z:
                z.writestr('version.py', '__version__ = "0.2.1"')
            with self.assertRaisesRegex(ValueError, 'missing files'):
                updater.validate_archive(package, '0.2.1')

    def test_download_verifies_checksum_and_stays_out_of_app(self):
        payload = fake_release_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        release = updater.Release(version='0.2.1', html_url='https://github.com/test/releases/x',
                                  asset_url='https://github.com/test/asset.zip',
                                  checksum_url='https://github.com/test/checksum',
                                  body='', repo='maxlpld/uhv-sentinel',
                                  asset_name='uhv-sentinel-release.zip')
        with tempfile.TemporaryDirectory() as tmp:
            with patch('updater.data_dir', return_value=Path(tmp)):
                with patch('updater._load_url', side_effect=[
                    (digest + '  uhv-sentinel-release.zip\n').encode(), payload]):
                    result = updater.prepare_release(release)
                self.assertEqual(result['sha256'], digest)
                self.assertTrue(Path(result['archive']).is_file())
                self.assertTrue(Path(result['archive']).is_relative_to(tmp))
                Path(result['archive']).unlink()
                with patch('updater._load_url', side_effect=[
                    ('a' * 64 + '  uhv-sentinel-release.zip\n').encode(), payload]):
                    with self.assertRaisesRegex(ValueError, 'SHA-256'):
                        updater.prepare_release(release)

    def test_apply_update_replaces_only_app_and_keeps_data(self):
        payload = fake_release_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'installation'
            app = root / 'app'
            app.mkdir(parents=True)
            (app / 'updater.py').write_text('previous release', encoding='utf-8')
            (app / 'release_config.json').write_text(json.dumps(updater.release_settings()), encoding='utf-8')
            (root / 'install.json').write_text('{}')
            data = Path(tmp) / 'data'
            (data / 'updates').mkdir(parents=True)
            history = data / 'pressure_history.sqlite3'
            history.write_bytes(b'test data, do not edit')
            package = data / 'updates' / 'pending-test.zip'
            package.write_bytes(payload)
            exe = root / 'runtime' / 'bin' / 'python'
            exe.parent.mkdir(parents=True)
            exe.touch()
            with patch('updater.install_dir', return_value=root), \
                 patch('updater.data_dir', return_value=data), \
                 patch('updater.__file__', str(app / 'updater.py')), \
                 patch('app_paths.runtime_python', return_value=exe), \
                 patch('subprocess.run'):
                updater.apply_prepared_update(archive=str(package), version='0.2.1', sha256=digest)
            self.assertEqual(history.read_bytes(), b'test data, do not edit')
            self.assertEqual((app / 'version.py').read_text(), '__version__ = "0.2.1"\n')
            self.assertEqual((root / 'previous_app' / 'updater.py').read_text(), 'previous release')
            self.assertFalse(package.exists())
            self.assertTrue(json.loads((data / 'last_update.json').read_text())['success'])

    def test_release_info_parser_offline(self):
        assets = [{'name': x, 'browser_download_url': 'https://github.com/example/' + x}
                  for x in ('uhv-sentinel-release.zip', 'SHA256SUMS.txt')]
        payload = json.dumps({'tag_name': 'v0.2.2', 'html_url': 'https://github.com/a/b',
                              'assets': assets, 'body': 'Fixes'}).encode()
        with patch('updater._load_url', return_value=payload):
            release = updater.latest_release()
        self.assertTrue(updater.newer_release(release))
        self.assertEqual(release.version, '0.2.2')


class InstallTests(unittest.TestCase):
    def test_install_reinstall_uninstall_preserve_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'installed'
            data = Path(tmp) / 'user-data'
            data.mkdir()
            history = data / 'pressure_history_2026-W47.sqlite3'
            history.write_bytes(b'precious recordings')
            executable = root / 'runtime' / 'bin' / 'python'
            def create_runtime(_self, _directory):
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_bytes(b'fake-python')
            with patch('installer.install_dir', return_value=root), \
                 patch('installer.data_dir', return_value=data), \
                 patch('installer.runtime_python', return_value=executable), \
                 patch('installer.create_shortcuts'), \
                 patch('installer.remove_shortcuts'), \
                 patch('installer.venv.EnvBuilder.create', create_runtime), \
                 patch('installer.subprocess.run'):
                installer.install(launch=False)
                self.assertTrue((root / 'app' / 'uhv_sentinel.py').is_file())
                self.assertTrue((root / 'install.json').is_file())
                installer.install(launch=False)
                self.assertTrue((root / 'previous_app' / 'uhv_sentinel.py').is_file())
                installer.uninstall()
                self.assertFalse(root.exists())
                self.assertEqual(history.read_bytes(), b'precious recordings')


if __name__ == '__main__':
    unittest.main()
