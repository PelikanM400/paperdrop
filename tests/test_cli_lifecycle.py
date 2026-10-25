"""CLI lifecycle routes. No real upgrades, browser, desktop or scheduler changes."""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import support
from paperdrop import cli
from paperdrop.config import example_config


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = pathlib.Path(self.temp.name) / 'config.json'

    def invoke(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(['--config', str(self.config), *arguments])
        return code, out.getvalue(), err.getvalue()

    def test_ui_dispatches_before_missing_config_validation(self):
        with patch('paperdrop.ui.serve', return_value=0) as serve:
            code, _, err = self.invoke('ui', '--no-open', '--port', '0')
        self.assertEqual(code, 0, err)
        serve.assert_called_once_with(str(self.config), port=0, open_browser=False)
        self.assertFalse(self.config.exists())
    def test_first_interactive_install_opens_ui_without_enabling_schedule(self):
        with patch.object(cli, '_auto_ui_allowed', return_value=True, create=True), \
                patch('paperdrop.ui.serve', return_value=0) as serve, \
                patch.object(cli, '_install', side_effect=AssertionError('not confirmed')):
            code, _, err = self.invoke('install')
        self.assertEqual(code, 0, err)
        serve.assert_called_once_with(str(self.config))
        self.assertFalse(self.config.exists())

    def test_no_ui_install_keeps_the_existing_noninteractive_schedule_route(self):
        self.config.write_text(json.dumps(example_config()))
        with patch.object(cli, '_auto_ui_allowed', return_value=True, create=True), \
                patch.object(cli, '_install', return_value=0) as install, \
                patch('paperdrop.ui.serve') as serve:
            code, _, err = self.invoke('install', '--no-ui')
        self.assertEqual(code, 0, err)
        self.assertEqual(install.call_count, 1)
        serve.assert_not_called()

    def test_existing_interactive_install_opens_ui_only_after_success(self):
        self.config.write_text(json.dumps(example_config()))
        for result in (0, 1):
            with self.subTest(result=result), \
                    patch.object(cli, '_auto_ui_allowed', return_value=True, create=True), \
                    patch.object(cli, '_install', return_value=result), \
                    patch('paperdrop.ui.serve', return_value=0) as serve:
                code, _, _ = self.invoke('install')
                self.assertEqual(code, result)
                self.assertEqual(serve.call_count, 1 if result == 0 else 0)
    def upgrade_module(self, changed=True, failure=False):
        class UpgradeError(Exception):
            pass
        def perform():
            if failure:
                raise UpgradeError('upgrade failed safely')
            return {'ok': True, 'changed': changed, 'version': '0.3.1', 'python': '/isolated/bin/python'}
        return types.SimpleNamespace(UpgradeError=UpgradeError, upgrade_software=perform)

    def test_upgrade_needs_no_config_and_launches_the_new_python_after_success(self):
        with patch.dict(sys.modules, {'paperdrop.upgrade': self.upgrade_module()}), \
                patch.object(cli, '_auto_ui_allowed', return_value=True), \
                patch.object(cli.subprocess, 'call', return_value=0) as launch:
            code, _, err = self.invoke('upgrade')
        self.assertEqual(code, 0, err)
        launch.assert_called_once_with(['/isolated/bin/python', '-I', '-m', 'paperdrop', '--config', str(self.config), 'ui'])
        self.assertFalse(self.config.exists())

    def test_upgrade_skip_modes_never_start_ui(self):
        for flags, changed, allowed in ((['--no-ui'], True, True), (['--json'], True, True), ([], False, True), ([], True, False)):
            with self.subTest(flags=flags, changed=changed, allowed=allowed), \
                    patch.dict(sys.modules, {'paperdrop.upgrade': self.upgrade_module(changed)}), \
                    patch.object(cli, '_auto_ui_allowed', return_value=allowed), \
                    patch.object(cli.subprocess, 'call') as launch:
                code, out, err = self.invoke('upgrade', *flags)
                self.assertEqual(code, 0, err)
                launch.assert_not_called()
                if '--json' in flags:
                    self.assertEqual(json.loads(out)['version'], '0.3.1')

    def test_upgrade_failure_does_not_launch_ui(self):
        with patch.dict(sys.modules, {'paperdrop.upgrade': self.upgrade_module(failure=True)}), \
                patch.object(cli, '_auto_ui_allowed', return_value=True), \
                patch.object(cli.subprocess, 'call') as launch:
            code, _, err = self.invoke('upgrade')
        self.assertEqual(code, 1)
        self.assertIn('upgrade failed safely', err)
        launch.assert_not_called()

    def test_automatic_ui_requires_interactive_desktop_and_rejects_ssh_and_json(self):
        args = types.SimpleNamespace(no_ui=False, json=False)
        terminal = types.SimpleNamespace(isatty=lambda: True)
        with patch.dict(os.environ, {}, clear=True), patch.object(cli.sys, 'platform', 'linux'), \
                patch.object(cli.sys, 'stdin', terminal), patch.object(cli.sys, 'stdout', terminal):
            self.assertFalse(cli._auto_ui_allowed(args))
            with patch.dict(os.environ, {'DISPLAY': ':0'}):
                self.assertTrue(cli._auto_ui_allowed(args))
                for name in ('SSH_CONNECTION', 'CI'):
                    with patch.dict(os.environ, {name: 'yes'}):
                        self.assertFalse(cli._auto_ui_allowed(args))
                args.json = True
                self.assertFalse(cli._auto_ui_allowed(args))


if __name__ == '__main__':
    unittest.main()
