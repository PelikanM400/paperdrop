"""CLI behaviour, driven through a real fetch against a local loopback server."""
from __future__ import annotations

import http.server
import io
import json
import pathlib
import tempfile
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import support

from paperdrop.cli import main
from paperdrop.util import sha256_hex

IMAGE = support.make_image((800, 450))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith('/desk.json'):
            manifest = {'date': '2030-01-02', 'published_at': '2030-01-02T04:30:00+08:00',
                        'timezone': 'Asia/Shanghai', 'photo_id': 'abcdefghijk',
                        'image_url': self.server.base + '/desk.jpg',
                        'sha256': sha256_hex(IMAGE), 'width': 800, 'height': 450,
                        'size_bytes': len(IMAGE)}
            return self._send(200, json.dumps(manifest).encode(), 'application/json')
        if self.path.startswith('/desk.jpg'):
            return self._send(200, IMAGE, 'image/jpeg')
        if self.path.startswith('/repo'):
            return self._send(200, b'<html>a repository page</html>', 'text/html')
        return self._send(404, b'', 'text/plain')


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)
        self.config_path = self.root / 'config.json'
        self.platform = support.FakePlatform()
        self.patcher = patch('paperdrop.cli.detect_platform', return_value=self.platform)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write_config(self, url=None, mode='manifest'):
        payload = {
            'source': {'mode': mode, 'url': url or self.server.base + '/desk.json',
                       'image_mode_acknowledged': mode == 'image'},
            'cache_dir': str(self.root / 'cache'),
            'state_file': str(self.root / 'state' / 'state.json'),
            'log': {'path': str(self.root / 'state' / 'paperdrop.log')},
            'http': {'allow_http': True, 'retries': 1},
        }
        self.config_path.write_text(json.dumps(payload), encoding='utf-8')
        return payload

    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['--config', str(self.config_path), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_print_example_config_needs_no_config_file(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(['--print-example-config'])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload['source']['mode'], 'manifest')
        self.assertIn('_comment', payload)

    def test_missing_config_exits_two_with_a_pointer(self):
        missing = self.root / 'nope.json'
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(['--config', str(missing), 'run'])
        self.assertEqual(code, 2)
        self.assertIn('nope.json', err.getvalue())

    def test_run_then_repeat(self):
        self.write_config()
        code, out, _ = self.invoke('run', '--json')
        self.assertEqual(code, 0)
        first = json.loads(out)
        self.assertEqual(first['outcome'], 'updated')
        self.assertTrue(first['downloaded'])
        self.assertEqual((self.root / 'cache' / 'current.jpg').read_bytes(), IMAGE)
        self.assertEqual(len(self.platform.applied), 1)

        code, out, _ = self.invoke('run', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['outcome'], 'unchanged')
        self.assertEqual(len(self.platform.applied), 1, 'must not re-apply the same day')

    def test_update_forces_a_new_run(self):
        self.write_config()
        self.invoke('run')
        code, out, _ = self.invoke('update', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['outcome'], 'updated')

    def test_update_in_image_mode_exits_zero_and_flags_the_weak_guarantee(self):
        self.write_config(url=self.server.base + '/desk.jpg', mode='image')
        code, out, _ = self.invoke('run', '--json')
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload['outcome'], 'updated')
        self.assertTrue(any('no consistency guarantee' in w for w in payload['warnings']))

    def test_a_repository_page_is_refused_with_advice(self):
        self.write_config(url=self.server.base + '/repo')
        code, out, err = self.invoke('run')
        self.assertEqual(code, 1)
        self.assertIn('manifest', out + err)

    def test_status_reports_source_cache_and_verification_level(self):
        self.write_config()
        self.invoke('run')
        code, out, _ = self.invoke('status', '--json')
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload['manifest_consistency'], 'sha256 verified')
        self.assertTrue(payload['current_image'].endswith('current.jpg'))
        self.assertEqual(payload['source']['mode'], 'manifest')

    def test_status_in_image_mode_says_the_guarantee_is_weak(self):
        self.write_config(url=self.server.base + '/desk.jpg', mode='image')
        code, out, _ = self.invoke('status', '--json')
        self.assertEqual(code, 0)
        self.assertIn('NONE', json.loads(out)['manifest_consistency'])

    def test_install_and_uninstall_report_and_keep_the_cache(self):
        self.write_config()
        self.invoke('run')
        with patch.object(type(self.platform), 'install', create=True,
                          return_value=['/tmp/fake/unit']):
            code, out, _ = self.invoke('install', '--json')
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)['installed'], ['/tmp/fake/unit'])
        with patch.object(type(self.platform), 'uninstall', create=True,
                          return_value=['/tmp/fake/unit']):
            code, out, _ = self.invoke('uninstall', '--json')
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)['purged'], [])
        self.assertTrue((self.root / 'cache' / 'current.jpg').exists(),
                        'uninstall must keep the cached wallpaper by default')

    def test_uninstall_purge_deletes_the_cache(self):
        self.write_config()
        self.invoke('run')
        with patch.object(type(self.platform), 'uninstall', create=True, return_value=[]):
            code, out, _ = self.invoke('uninstall', '--purge', '--json')
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)['purged'])
        self.assertFalse((self.root / 'cache' / 'current.jpg').exists())

    def test_doctor_runs_and_reports_each_check(self):
        self.write_config()
        code, out, _ = self.invoke('doctor', '--json')
        rows = json.loads(out)
        names = [row['check'] for row in rows]
        self.assertIn('Pillow (image verification)', names)
        self.assertIn('cache directory writable', names)
        self.assertTrue(all(row['ok'] for row in rows if row['check'] == 'cache directory writable'))
        self.assertIn(code, (0, 1))

    def test_help_is_shown_when_no_command_is_given(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main([])
        self.assertEqual(code, 0)
        self.assertIn('run', out.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
