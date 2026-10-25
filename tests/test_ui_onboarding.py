"""First-run settings stay in memory until an explicit, validated apply."""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import socketserver
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

import support  # noqa: F401
from paperdrop import ui
from paperdrop.config import example_config
from test_ui import request


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.root_path = pathlib.Path(self.home.name)
        self.config_path = self.root_path / 'settings' / 'config.json'
        environment = mock.patch.dict(os.environ, {'PAPERDROP_HOME': self.home.name})
        environment.start()
        self.addCleanup(environment.stop)

    def start_server(self):
        handler = type('IsolatedHandler', (ui.Handler,), {
            'config_path': self.config_path, 'token': 'onboarding-token'})
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.root = 'http://127.0.0.1:%d' % server.server_address[1]

    def url(self, path):
        return self.root + path + '?t=onboarding-token'

    def payload(self):
        return {'daily_time': example_config()['schedule']['daily_time'],
                'source_url': ui.PRESETS[0][1]}

    def manifest(self, url=None):
        return {'kind': 'manifest', 'rows': [], 'url': url or self.payload()['source_url'], 'error': None}

    def test_serve_accepts_a_missing_config_without_creating_anything(self):
        class StubServer:
            server_address = ('127.0.0.1', 12345)

            def __init__(self, address, handler):
                self.handler = handler

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        output = io.StringIO()
        with mock.patch.object(ui.socketserver, 'ThreadingTCPServer', StubServer), \
                mock.patch.object(ui, '_run_cli') as run, \
                contextlib.redirect_stdout(output):
            self.assertEqual(ui.serve(self.config_path, port=0, open_browser=False), 0)
        run.assert_not_called()
        self.assertFalse(self.config_path.parent.exists())
        self.assertIn('尚未配置', output.getvalue())
        self.assertIn('Not configured yet', output.getvalue())

    def test_first_check_of_unchanged_defaults_is_read_only_and_requires_install(self):
        self.start_server()
        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()) as classify, \
                mock.patch.object(ui, '_run_cli') as run:
            status, body = request(self.url('/api/check-preview'), 'POST', self.payload())
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['source_check']['ok'])
        self.assertTrue(result['onboarding'])
        self.assertTrue(result['needs_install'])
        classify.assert_called_once_with(self.payload()['source_url'], False)
        run.assert_not_called()
        self.assertFalse(self.config_path.parent.exists())

    def test_first_apply_of_unchanged_defaults_creates_private_config_and_installs(self):
        self.start_server()
        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()) as classify, \
                mock.patch.object(ui, '_run_cli', return_value={'ok': True, 'exit_code': 0, 'output': 'installed'}) as run:
            status, body = request(self.url('/api/apply'), 'POST', self.payload())
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertTrue(result['ok'])
        self.assertIsNone(result['backup'])
        self.assertTrue(result['install']['ok'])
        saved = json.loads(self.config_path.read_text())
        self.assertEqual(saved['source']['url'], ui.PRESETS[0][1])
        self.assertEqual(saved['schedule'], example_config()['schedule'])
        self.assertEqual(self.config_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.config_path.parent.glob('*.tmp-*')), [])
        classify.assert_called_once_with(self.payload()['source_url'], False)
        run.assert_called_once_with(self.config_path, 'install')

    def test_failed_first_install_uninstalls_new_schedule_then_removes_new_config(self):
        self.start_server()
        schedule = self.root_path / 'new-schedule'
        calls = []

        def cli(path, command):
            calls.append(command)
            self.assertTrue(path.is_file(), 'uninstall needs the candidate config')
            if command == 'install':
                schedule.write_text('partial schedule')
                return {'ok': False, 'exit_code': 1, 'output': 'installation failed'}
            self.assertEqual(command, 'uninstall')
            schedule.unlink()
            return {'ok': True, 'exit_code': 0, 'output': 'removed'}

        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()), \
                mock.patch.object(ui, '_run_cli', side_effect=cli):
            status, body = request(self.url('/api/apply'), 'POST', self.payload())
        self.assertEqual(status, 500, body)
        result = json.loads(body)
        self.assertFalse(result['ok'])
        self.assertIn('rolled back', result['error'])
        self.assertIn('installation failed', result['error'], 'the real installer output must be reported')
        self.assertTrue(result['restore']['ok'])
        self.assertEqual(calls, ['install', 'uninstall'])
        self.assertFalse(schedule.exists())
        self.assertFalse(self.config_path.exists())
        self.assertEqual(list(self.config_path.parent.glob('*.tmp-*')), [])

    def test_onboarding_apply_refuses_a_config_created_since_status(self):
        self.start_server()
        self.assertEqual(request(self.url('/api/status'))[0], 200)
        self.config_path.parent.mkdir()
        existing = example_config()
        existing['schedule']['daily_time'] = '11:42'
        existing['source']['url'] = 'https://fixture.invalid/existing.json'
        original = json.dumps(existing)
        self.config_path.write_text(original)
        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()), \
                mock.patch.object(ui, '_run_cli') as run:
            status, body = request(self.url('/api/apply'), 'POST', {**self.payload(), 'onboarding': True})
        self.assertEqual(status, 400, body)
        self.assertIn('already exists', json.loads(body)['error'])
        self.assertEqual(self.config_path.read_text(), original)
        run.assert_not_called()

    def test_unconfigured_quick_actions_never_invoke_the_cli(self):
        self.start_server()
        with mock.patch.object(ui, '_run_cli') as run:
            for endpoint, body in [('/api/run', {'force': False}), ('/api/run', {'force': True}),
                                   ('/api/schedule', {'action': 'pause'}),
                                   ('/api/schedule', {'action': 'resume'})]:
                with self.subTest(endpoint=endpoint, body=body):
                    status, result = request(self.url(endpoint), 'POST', body)
                    self.assertEqual(status, 400, result)
                    self.assertIn('配置', json.loads(result)['error'])
        run.assert_not_called()
        self.assertFalse(self.config_path.parent.exists())

    def test_internal_install_argv_disables_recursive_ui(self):
        result = mock.Mock(returncode=0, stdout='installed', stderr='')
        with mock.patch.object(ui.subprocess, 'run', return_value=result) as run:
            self.assertTrue(ui._run_cli(self.config_path, 'install')['ok'])
        self.assertEqual(run.call_args.args[0], [ui.sys.executable, '-m', 'paperdrop',
                                              '--config', str(self.config_path), 'install', '--no-ui'])

    def test_readback_failure_removes_new_config_without_installing(self):
        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()), \
                mock.patch.object(ui, 'load_config', side_effect=ValueError('readback failed')), \
                mock.patch.object(ui, '_run_cli') as run:
            with self.assertRaisesRegex(ValueError, 'readback failed'):
                ui.apply_changes(self.config_path, **{'daily_time': self.payload()['daily_time'],
                                                    'source_url': self.payload()['source_url']})
        self.assertFalse(self.config_path.exists())
        run.assert_not_called()

    def test_install_exception_also_uninstalls_and_removes_new_config(self):
        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()), \
                mock.patch.object(ui, '_run_cli', side_effect=[OSError('child failed'),
                    {'ok': True, 'exit_code': 0, 'output': 'removed'}]) as run:
            result = ui.apply_changes(self.config_path, self.payload()['daily_time'], self.payload()['source_url'])
        self.assertFalse(result['ok'])
        self.assertIn('child failed', result['install']['output'])
        self.assertTrue(result['restore']['ok'])
        self.assertEqual([call.args[1] for call in run.call_args_list], ['install', 'uninstall'])
        self.assertFalse(self.config_path.exists())

    def test_uninstall_exception_is_reported_and_new_config_is_still_removed(self):
        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()), \
                mock.patch.object(ui, '_run_cli', side_effect=[
                    {'ok': False, 'exit_code': 1, 'output': 'partial install'}, OSError('cleanup failed')]):
            result = ui.apply_changes(self.config_path, self.payload()['daily_time'], self.payload()['source_url'])
        self.assertFalse(result['ok'])
        self.assertFalse(result['restore']['ok'])
        self.assertIn('cleanup failed', result['restore']['output'])
        self.assertIn('cleanup is incomplete', result['error'])
        self.assertNotIn('rolled back', result['error'])
        self.assertFalse(self.config_path.exists())

    def test_failed_atomic_publication_does_not_leave_a_new_config(self):
        original_fsync = os.fsync

        def fail_directory_fsync(descriptor):
            import stat
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError('directory sync failed')
            return original_fsync(descriptor)

        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()), \
                mock.patch.object(ui.os, 'fsync', side_effect=fail_directory_fsync), \
                mock.patch.object(ui, '_run_cli') as run:
            with self.assertRaisesRegex(OSError, 'directory sync failed'):
                ui.apply_changes(self.config_path, self.payload()['daily_time'], self.payload()['source_url'])
        run.assert_not_called()
        self.assertFalse(self.config_path.exists())
        self.assertEqual(list(self.config_path.parent.glob('*.tmp-*')), [])

    def test_concurrent_create_never_overwrites_the_winner(self):
        self.start_server()
        self.config_path.parent.mkdir()
        winners = []

        def cli(path, command):
            winners.append(path.read_text())
            return {'ok': True, 'exit_code': 0, 'output': 'installed'}

        def competitor():
            # Arrive after the UI validated its draft but before publication.
            original = ui._atomic_write
            def interleaved(*args, **kwargs):
                if not os.path.lexists(self.config_path):
                    self.config_path.write_text('{"competing": true}\n')
                return original(*args, **kwargs)
            return mock.patch.object(ui, '_atomic_write', side_effect=interleaved)

        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()), \
                mock.patch.object(ui, '_run_cli', side_effect=cli), competitor():
            status, body = request(self.url('/api/apply'), 'POST', {**self.payload(), 'onboarding': True})
        self.assertEqual(status, 400, body)
        self.assertEqual(json.loads(body)['error'].count('already exists'), 1)
        self.assertEqual(self.config_path.read_text(), '{"competing": true}\n')
        self.assertEqual(winners, [])

    def test_existing_config_is_never_treated_as_onboarding(self):
        self.config_path.parent.mkdir()
        existing = example_config()
        existing['extra_note'] = 'kept'
        existing['source']['url'] = 'https://fixture.invalid/existing.json'
        self.config_path.write_text(json.dumps(existing))
        self.start_server()
        status, body = request(self.url('/api/status'))
        self.assertEqual(status, 500, 'unknown keys must fail closed, not look like a new install')
        self.assertIn('Unknown configuration key', json.loads(body)['error'])

    def test_occupied_targets_are_never_overwritten(self):
        for kind in ('directory', 'dangling-symlink', 'regular-file'):
            with self.subTest(kind=kind):
                home = tempfile.TemporaryDirectory()
                self.addCleanup(home.cleanup)
                config_path = pathlib.Path(home.name) / 'config.json'
                if kind == 'directory':
                    config_path.mkdir()
                elif kind == 'dangling-symlink':
                    config_path.symlink_to(pathlib.Path(home.name) / 'missing.json')
                else:
                    config_path.write_text('{"existing": true}\n')
                with mock.patch.object(ui, 'classify_source', return_value=self.manifest()), \
                        mock.patch.object(ui, '_run_cli') as run:
                    with self.assertRaises(Exception):
                        ui.apply_changes(config_path, self.payload()['daily_time'],
                                         self.payload()['source_url'], create_only=True)
                run.assert_not_called()
                if kind == 'directory':
                    self.assertTrue(config_path.is_dir())
                elif kind == 'dangling-symlink':
                    self.assertTrue(config_path.is_symlink())
                    self.assertFalse(config_path.exists())
                else:
                    self.assertEqual(config_path.read_text(), '{"existing": true}\n')

    def test_existing_config_apply_keeps_other_fields_and_skips_onboarding(self):
        self.config_path.parent.mkdir()
        existing = example_config()
        existing['_comment'] = 'keep me'
        existing['source']['url'] = 'https://fixture.invalid/existing.json'
        self.config_path.write_text(json.dumps(existing), encoding='utf-8')
        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()), \
                mock.patch.object(ui, '_run_cli', return_value={'ok': True, 'exit_code': 0, 'output': 'ok'}):
            result = ui.apply_changes(self.config_path, '06:15', 'https://fixture.invalid/other.json')
        self.assertTrue(result['ok'], result)
        saved = json.loads(self.config_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['_comment'], 'keep me')
        self.assertEqual(saved['source']['url'], 'https://fixture.invalid/other.json')
        self.assertIsNotNone(result['backup'])

    def test_first_apply_without_a_check_still_requires_a_manifest(self):
        image_url = 'https://fixture.invalid/today.jpg'
        verdict = {'kind': 'image', 'rows': [], 'url': image_url, 'error': None}
        with mock.patch.object(ui, 'classify_source', return_value=verdict), \
                mock.patch.object(ui, '_run_cli') as run:
            with self.assertRaisesRegex(ValueError, '清单'):
                ui.apply_changes(self.config_path, self.payload()['daily_time'],
                                 image_url, create_only=True)
        run.assert_not_called()
        self.assertFalse(self.config_path.exists())

    def test_onboarding_requests_keep_host_token_and_csp_guards(self):
        self.start_server()
        self.assertEqual(request(self.root + '/?t=onboarding-token', host='evil.example')[0], 403)
        self.assertEqual(request(self.url('/api/status'), host='evil.example')[0], 403)
        self.assertEqual(request(self.root + '/api/status')[0], 403)
        status, page = request(self.url('/'))
        self.assertEqual(status, 200)
        with urllib.request.urlopen(self.url('/'), timeout=15) as response:
            policy = response.headers.get('Content-Security-Policy')
        self.assertIn("default-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)

    def test_first_manifest_check_and_apply_are_never_implicit(self):
        self.start_server()
        calls = []
        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()) as classify, \
                mock.patch.object(ui, '_run_cli', side_effect=lambda path, *a: calls.append(a) or
                                  {'ok': True, 'exit_code': 0, 'output': 'ok'}):
            self.assertEqual(request(self.url('/api/status'))[0], 200)
            self.assertEqual(request(self.url('/api/check-preview'), 'POST', self.payload())[0], 200)
            self.assertEqual(calls, [])
            self.assertFalse(self.config_path.exists())
            self.assertEqual(request(self.url('/api/apply'), 'POST', self.payload())[0], 200)
        self.assertEqual(calls, [('install',)])

    def test_serve_keeps_a_symlinked_existing_config_and_refuses_a_dangling_one(self):
        class StubServer:
            server_address = ('127.0.0.1', 12345)

            def __init__(self, address, handler):
                self.handler = handler

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        real = self.root_path / 'real-config.json'
        real.write_text(json.dumps(example_config()), encoding='utf-8')
        link = self.root_path / 'link.json'
        link.symlink_to(real)
        with mock.patch.object(ui.socketserver, 'ThreadingTCPServer', StubServer), \
                mock.patch.object(ui, '_run_cli') as run, \
                contextlib.redirect_stdout(io.StringIO()):
            ui.serve(link, port=0, open_browser=False)
        self.assertEqual(ui.Handler.config_path, real.resolve())
        run.assert_not_called()

        dangling = self.root_path / 'dangling.json'
        dangling.symlink_to(self.root_path / 'missing.json')
        bound = []
        with mock.patch.object(ui.socketserver, 'ThreadingTCPServer',
                               side_effect=lambda *a: bound.append(a) or StubServer(*a)):
            with self.assertRaises(Exception):
                ui.serve(dangling, port=0, open_browser=False)
        self.assertEqual(bound, [], 'a dangling symlink must not start a server')
        self.assertFalse((self.root_path / 'missing.json').exists())

    def test_unremovable_new_config_is_reported_honestly(self):
        from paperdrop import ui as ui_module
        original = ui_module.pathlib.Path.unlink

        def locked(self, *args, **kwargs):
            if str(self) == str(self_config):
                raise OSError('file is busy')
            return original(self, *args, **kwargs)

        self_config = self.config_path
        with mock.patch.object(ui, 'classify_source', return_value=self.manifest()), \
                mock.patch.object(ui, '_run_cli', side_effect=[
                    {'ok': False, 'exit_code': 1, 'output': 'install broke'},
                    {'ok': False, 'exit_code': 1, 'output': 'uninstall broke'}]), \
                mock.patch.object(ui_module.pathlib.Path, 'unlink', locked):
            result = ui.apply_changes(self_config, self.payload()['daily_time'],
                                      self.payload()['source_url'])
        self.assertFalse(result['ok'])
        self.assertIn('could not be removed', result['error'])
        self.assertIn('delete it manually', result['error'])
        self.assertNotIn('rolled back', result['error'])
        self.assertTrue(self_config.exists(), 'the test models a file we could not delete')

    def test_unconfigured_log_is_empty_instead_of_an_error(self):
        self.start_server()
        status, body = request(self.url('/api/log'))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'text': '', 'empty': True})
        self.assertFalse(self.config_path.parent.exists())

    def test_missing_status_is_an_in_memory_preset_without_commands_or_network(self):
        self.start_server()
        with mock.patch.object(ui, 'classify_source') as classify, \
                mock.patch.object(ui, '_run_cli') as run, \
                mock.patch.object(ui, '_schedule_state') as schedule:
            status, body = request(self.url('/api/status'))
        self.assertEqual(status, 200, body)
        result = json.loads(body)
        self.assertTrue(result['onboarding'])
        self.assertEqual(result['source']['url'], ui.PRESETS[0][1])
        self.assertEqual(result['schedule']['daily_time'], '07:35')
        self.assertEqual(result['schedule_state'], '尚未配置')
        self.assertEqual(result['state'], {})
        self.assertFalse(self.config_path.parent.exists())
        classify.assert_not_called()
        run.assert_not_called()
        schedule.assert_not_called()


if __name__ == '__main__':
    unittest.main()
