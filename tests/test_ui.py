"""Tests for the local settings page (``paperdrop ui``).

Everything stays on loopback: a real server on port 0, real HTTP requests, a temporary
``PAPERDROP_HOME`` and a config derived from ``config.example.json``. No test reaches
the internet; the one fetch test serves a manifest from a local directory.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import socketserver
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import support  # noqa: F401  (puts src/ on sys.path)

from paperdrop import ui


def request(url, method='GET', body=None, headers=None, host=None):
    data = json.dumps(body).encode() if body is not None else None
    prepared = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        prepared.add_header('Content-Type', 'application/json')
    for key, value in (headers or {}).items():
        prepared.add_header(key, value)
    if host:
        prepared.add_header('Host', host)
    try:
        with urllib.request.urlopen(prepared, timeout=15) as response:
            return response.status, response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode('utf-8', 'replace')


class SettingsPageTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.home_path = pathlib.Path(self.home.name)
        environment = mock.patch.dict(os.environ, {'PAPERDROP_HOME': str(self.home_path)})
        environment.start()
        self.addCleanup(environment.stop)

        self.config_dir = self.home_path / '.config' / 'paperdrop'
        self.config_dir.mkdir(parents=True)
        self.config_path = self.config_dir / 'config.json'
        base = json.loads((support.ROOT / 'config.example.json').read_text(encoding='utf-8'))
        base['log']['path'] = str(self.home_path / 'paperdrop.log')
        # The fixtures below are served from 127.0.0.1 over plain http.
        base['http']['allow_http'] = True
        self.write_config(base)
        os.chmod(self.config_path, 0o600)
        self.original_text = self.config_path.read_text(encoding='utf-8')

        ui.Handler.token = 'test-token'
        ui.Handler.config_path = self.config_path
        self.server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), ui.Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.root = 'http://127.0.0.1:%d' % self.server.server_address[1]

    # helpers -----------------------------------------------------------------
    def write_config(self, data):
        self.config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def url(self, path, token='test-token'):
        separator = '&' if '?' in path else '?'
        return '%s%s%st=%s' % (self.root, path, separator, token)

    def payload(self, **changes):
        config = json.loads(self.config_path.read_text(encoding='utf-8'))
        return {'daily_time': config['schedule']['daily_time'], 'source_url': config['source']['url'], **changes}

    def serve_directory(self, directory):
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(directory), **kwargs)

            def log_message(self, *args):
                pass

        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Quiet)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:%d' % server.server_address[1]

    # access control ----------------------------------------------------------
    def test_index_requires_a_token(self):
        status, _ = request(self.root + '/')
        self.assertEqual(status, 403)

    def test_index_rejects_a_wrong_token(self):
        status, _ = request(self.url('/', token='nope'))
        self.assertEqual(status, 403)

    def test_index_rejects_a_foreign_host_header(self):
        status, _ = request(self.url('/'), host='evil.example')
        self.assertEqual(status, 403)

    def test_index_serves_the_page(self):
        status, body = request(self.url('/'))
        self.assertEqual(status, 200)
        self.assertIn('PaperDrop 设置', body)
        self.assertNotIn('__PRESETS__', body)
        self.assertIn('Gitee 横版', body)

    def test_status_reports_the_config(self):
        status, body = request(self.url('/api/status'))
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload['config_path'], str(self.config_path))
        self.assertEqual(payload['schedule']['daily_time'], '07:35')
        self.assertEqual(payload['source']['mode'], 'manifest')

    # preview -----------------------------------------------------------------
    def test_preview_reports_the_changes(self):
        status, body = request(self.url('/api/preview'), 'POST', self.payload(daily_time='06:15'))
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['needs_install'])
        self.assertIn(['schedule.daily_time', '07:35', '06:15'], payload['changes'])

    def test_preview_rejects_a_bad_time(self):
        status, body = request(self.url('/api/preview'), 'POST', self.payload(daily_time='25:99'))
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload['ok'])
        self.assertIn('07:35', ' '.join(message['text'] for message in payload['messages']))
        self.assertEqual(self.config_path.read_text(encoding='utf-8'), self.original_text)

    def test_preview_rejects_a_repository_page(self):
        status, body = request(self.url('/api/preview'), 'POST',
                              self.payload(source_url='https://github.com/PelikanM400/pelikan_daily_wallpaper'))
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload['ok'])
        self.assertIn('repository', ' '.join(message['text'] for message in payload['messages']).lower())

    def test_preview_with_no_changes(self):
        status, body = request(self.url('/api/preview'), 'POST', self.payload())
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['changes'], [])

    # link check --------------------------------------------------------------
    def test_check_rejects_a_non_http_url(self):
        status, body = request(self.url('/api/check'), 'POST', {'source_url': 'ftp://example.com/a.jpg'})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload['ok'])
        self.assertIn('http', payload['error'])

    def test_check_reads_a_local_manifest(self):
        directory = self.home_path / 'served'
        directory.mkdir()
        manifest = support.manifest_for(support.make_image())
        (directory / 'desk.json').write_bytes(manifest)
        base = self.serve_directory(directory)
        status, body = request(self.url('/api/check'), 'POST', {'source_url': base + '/desk.json'})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload['ok'], payload)
        labels = [row[0] for row in payload['rows']]
        self.assertIn('识别为', labels)
        self.assertTrue(any('清单' in str(row[1]) for row in payload['rows']))

    # apply -------------------------------------------------------------------
    def test_apply_writes_backs_up_and_keeps_permissions(self):
        installed = mock.patch.object(ui, '_run_cli',
                                      return_value={'ok': True, 'exit_code': 0, 'output': 'installed'})
        with installed:
            status, body = request(self.url('/api/apply'), 'POST', self.payload(daily_time='06:15'))
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload['ok'], payload)
        written = json.loads(self.config_path.read_text(encoding='utf-8'))
        self.assertEqual(written['schedule']['daily_time'], '06:15')
        backup = pathlib.Path(payload['backup'])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_text(encoding='utf-8'), self.original_text)
        self.assertEqual(self.config_path.stat().st_mode & 0o777, 0o600)

    def test_apply_rejects_invalid_input_without_writing(self):
        status, body = request(self.url('/api/apply'), 'POST', self.payload(daily_time='99:99'))
        self.assertEqual(status, 400)
        self.assertIn('error', json.loads(body))
        self.assertEqual(self.config_path.read_text(encoding='utf-8'), self.original_text)
        self.assertEqual(list(self.config_dir.glob('config.json.bak-*')), [])

    def test_apply_without_changes_writes_nothing(self):
        status, body = request(self.url('/api/apply'), 'POST', self.payload())
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['changes'], [])
        self.assertIsNone(payload['backup'])
        self.assertEqual(list(self.config_dir.glob('config.json.bak-*')), [])

    def test_apply_reinstalls_when_the_time_changes(self):
        calls = {}

        def fake_run(config_path, *args, **kwargs):
            calls['args'] = args
            return {'ok': True, 'exit_code': 0, 'output': 'installed'}

        with mock.patch.object(ui, '_run_cli', side_effect=fake_run):
            status, body = request(self.url('/api/apply'), 'POST', self.payload(daily_time='06:15'))
        self.assertEqual(status, 200)
        self.assertEqual(calls['args'], ('install',))
        self.assertTrue(json.loads(body)['install']['ok'])

    def test_apply_rolls_back_when_install_fails(self):
        def failing_run(*args, **kwargs):
            return {'ok': False, 'exit_code': 1, 'output': 'systemctl failed'}

        with mock.patch.object(ui, '_run_cli', side_effect=failing_run):
            status, body = request(self.url('/api/apply'), 'POST', self.payload(daily_time='06:15'))
        self.assertEqual(status, 500)
        payload = json.loads(body)
        self.assertFalse(payload['ok'])
        self.assertIn('rolled back', payload['error'])
        self.assertEqual(self.config_path.read_text(encoding='utf-8'), self.original_text)

    # the review findings ------------------------------------------------------
    def test_install_failure_also_restores_the_previous_schedule(self):
        calls = []

        def failing_run(config_path, *args, **kwargs):
            calls.append(args)
            return {'ok': False, 'exit_code': 1, 'output': 'systemctl failed'}

        with mock.patch.object(ui, '_run_cli', side_effect=failing_run):
            request(self.url('/api/apply'), 'POST', self.payload(daily_time='06:15'))
        self.assertEqual(calls, [('install',), ('install',)],
                         'the failed install must be followed by a reinstall of the old schedule')

    def test_apply_refuses_an_image_link(self):
        directory = self.home_path / 'served-image'
        directory.mkdir()
        (directory / 'today.jpg').write_bytes(support.make_image())
        base = self.serve_directory(directory)
        status, body = request(self.url('/api/apply'), 'POST',
                               self.payload(source_url=base + '/today.jpg'))
        self.assertEqual(status, 400)
        self.assertIn('清单', json.loads(body)['error'])
        self.assertEqual(self.config_path.read_text(encoding='utf-8'), self.original_text)

    def test_apply_writes_a_manifest_url_with_manifest_mode(self):
        directory = self.home_path / 'served-manifest'
        directory.mkdir()
        (directory / 'desk.json').write_bytes(support.manifest_for(support.make_image()))
        base = self.serve_directory(directory)
        status, body = request(self.url('/api/apply'), 'POST',
                               self.payload(source_url=base + '/desk.json'))
        self.assertEqual(status, 200, body)
        written = json.loads(self.config_path.read_text(encoding='utf-8'))
        self.assertEqual(written['source']['mode'], 'manifest')
        self.assertTrue(written['source']['url'].endswith('/desk.json'))

    def test_check_rows_are_plain_text(self):
        directory = self.home_path / 'served-plain'
        directory.mkdir()
        (directory / 'desk.json').write_bytes(support.manifest_for(support.make_image()))
        base = self.serve_directory(directory)
        status, body = request(self.url('/api/check'), 'POST', {'source_url': base + '/desk.json'})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload['ok'])
        dumped = json.dumps(payload['rows'], ensure_ascii=False)
        self.assertNotIn('<span', dumped)
        self.assertNotIn('<code', dumped)
        # the page must escape every value it renders
        self.assertIn('esc(v)', ui.PAGE)
        self.assertNotIn("'</div><div>' + v + '</div>'", ui.PAGE)

    def test_backups_are_unique_even_in_the_same_second(self):
        installed = mock.patch.object(ui, '_run_cli',
                                      return_value={'ok': True, 'exit_code': 0, 'output': 'ok'})
        with installed:
            self.assertEqual(request(self.url('/api/apply'), 'POST',
                                     self.payload(daily_time='06:10'))[0], 200)
            self.assertEqual(request(self.url('/api/apply'), 'POST',
                                     self.payload(daily_time='06:20'))[0], 200)
        backups = sorted(self.config_dir.glob('config.json.bak-*'))
        self.assertEqual(len(backups), 2)
        for backup in backups:
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        contents = [backup.read_text(encoding='utf-8') for backup in backups]
        self.assertTrue(any('"daily_time": "07:35"' in text for text in contents))
        self.assertTrue(any('"daily_time": "06:10"' in text for text in contents))

    def test_config_is_tightened_to_0600(self):
        os.chmod(self.config_path, 0o644)
        installed = mock.patch.object(ui, '_run_cli',
                                      return_value={'ok': True, 'exit_code': 0, 'output': 'ok'})
        with installed:
            self.assertEqual(request(self.url('/api/apply'), 'POST',
                                     self.payload(daily_time='06:45'))[0], 200)
        self.assertEqual(self.config_path.stat().st_mode & 0o777, 0o600)

    def test_concurrent_applies_do_not_corrupt_or_lose_backups(self):
        installed = mock.patch.object(ui, '_run_cli',
                                      return_value={'ok': True, 'exit_code': 0, 'output': 'ok'})
        results = []

        def worker(time):
            results.append(request(self.url('/api/apply'), 'POST', self.payload(daily_time=time))[0])

        with installed:
            threads = [threading.Thread(target=worker, args=(value,)) for value in ('05:00', '06:00')]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(sorted(results), [200, 200])
        written = json.loads(self.config_path.read_text(encoding='utf-8'))
        self.assertIn(written['schedule']['daily_time'], ('05:00', '06:00'))
        self.assertEqual(len(list(self.config_dir.glob('config.json.bak-*'))), 2)
        self.assertEqual(list(self.config_dir.glob('*.tmp-*')), [])

    def test_an_unset_token_is_refused(self):
        original = ui.Handler.token
        ui.Handler.token = None
        try:
            status, _ = request(self.root + '/')
        finally:
            ui.Handler.token = original
        self.assertEqual(status, 403)

    def test_serve_binds_loopback_only(self):
        captured = {}

        class StubServer:
            def __init__(self, address, handler):
                captured['address'] = address
                raise KeyboardInterrupt

        with mock.patch.object(ui.socketserver, 'ThreadingTCPServer', StubServer):
            with self.assertRaises(KeyboardInterrupt):
                ui.serve(self.config_path, port=0, open_browser=False)
        self.assertEqual(captured['address'][0], '127.0.0.1')

    # the 0.2.1 cleanups -------------------------------------------------------
    def test_non_ascii_token_is_refused_without_a_traceback(self):
        status, _ = request(self.url('/', token='%E4%B8%AD%E6%96%87'))
        self.assertEqual(status, 403)

    def test_ipv6_host_header_is_understood(self):
        status, _ = request(self.url('/'), host='[::1]:8765')
        self.assertEqual(status, 200)

    def test_log_with_a_non_numeric_line_count(self):
        status, _ = request(self.url('/api/log?lines=abc'))
        self.assertEqual(status, 400)

    def test_page_escapes_apostrophes_and_does_not_double_escape(self):
        self.assertIn("'&#39;'", ui.PAGE)
        self.assertIn("['版本', s.version]", ui.PAGE)


    # request hygiene ---------------------------------------------------------
    def test_post_requires_json(self):
        prepared = urllib.request.Request(self.url('/api/preview'), data=b'daily_time=06:15', method='POST')
        prepared.add_header('Content-Type', 'application/x-www-form-urlencoded')
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(prepared, timeout=10)
        self.assertEqual(caught.exception.code, 415)

    def test_post_rejects_cross_site_requests(self):
        status, _ = request(self.url('/api/preview'), 'POST', self.payload(),
                            headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 403)

    def test_post_rejects_a_foreign_origin(self):
        status, _ = request(self.url('/api/preview'), 'POST', self.payload(),
                            headers={'Origin': 'https://evil.example'})
        self.assertEqual(status, 403)

    # actions -----------------------------------------------------------------
    def test_run_invokes_the_cli(self):
        calls = {}

        def fake_run(config_path, *args, **kwargs):
            calls['args'] = args
            return {'ok': True, 'exit_code': 0, 'output': '{"status": "unchanged"}'}

        with mock.patch.object(ui, '_run_cli', side_effect=fake_run):
            status, body = request(self.url('/api/run'), 'POST', {'force': False})
        self.assertEqual(status, 200)
        self.assertEqual(calls['args'], ('run',))
        self.assertTrue(json.loads(body)['ok'])

    def test_run_force_passes_force(self):
        calls = {}

        def fake_run(config_path, *args, **kwargs):
            calls['args'] = args
            return {'ok': True, 'exit_code': 0, 'output': 'ok'}

        with mock.patch.object(ui, '_run_cli', side_effect=fake_run):
            request(self.url('/api/run'), 'POST', {'force': True})
        self.assertEqual(calls['args'], ('run', '--force'))

    def test_schedule_pause_and_resume(self):
        seen = []

        def fake_run(config_path, *args, **kwargs):
            seen.append(args)
            return {'ok': True, 'exit_code': 0, 'output': 'done'}

        with mock.patch.object(ui, '_run_cli', side_effect=fake_run):
            self.assertEqual(request(self.url('/api/schedule'), 'POST', {'action': 'pause'})[0], 200)
            self.assertEqual(request(self.url('/api/schedule'), 'POST', {'action': 'resume'})[0], 200)
        self.assertEqual(seen, [('pause',), ('resume',)])

    def test_schedule_rejects_an_unknown_action(self):
        status, _ = request(self.url('/api/schedule'), 'POST', {'action': 'reboot'})
        self.assertEqual(status, 400)

    # log ---------------------------------------------------------------------
    def test_log_tail_returns_the_last_lines(self):
        log_file = self.home_path / 'paperdrop.log'
        log_file.write_text('\n'.join('line-%d' % index for index in range(50)) + '\n', encoding='utf-8')
        status, body = request(self.url('/api/log?lines=5'))
        self.assertEqual(status, 200)
        text = json.loads(body)['text']
        self.assertEqual(text.splitlines(), ['line-45', 'line-46', 'line-47', 'line-48', 'line-49'])

    def test_unknown_endpoint_is_404(self):
        status, _ = request(self.url('/api/nope'))
        self.assertEqual(status, 404)


class CliWiringTests(unittest.TestCase):
    def test_ui_is_a_subcommand(self):
        from paperdrop.cli import build_parser
        args = build_parser().parse_args(['ui', '--port', '9001', '--no-open'])
        self.assertEqual(args.command, 'ui')
        self.assertEqual(args.port, 9001)
        self.assertTrue(args.no_open)

    def test_existing_commands_are_unchanged(self):
        from paperdrop.cli import build_parser
        parser = build_parser()
        for command in ('run', 'update', 'status', 'install', 'uninstall', 'pause',
                        'resume', 'doctor', 'apply-cached', 'watch'):
            self.assertEqual(parser.parse_args([command]).command, command)


if __name__ == '__main__':
    unittest.main()
