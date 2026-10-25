"""Homebrew ownership and stable scheduler paths; never touch a host install."""
from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import support
# Re-use the canonical pipx fixture without changing its release data.
import test_upgrade as pipx_tests


class HomebrewFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='paperdrop-homebrew-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.package = self.root / 'package' / 'paperdrop'
        self.package.mkdir(parents=True)
        self.marker = self.package / '_homebrew.py'

    def distribution(self):
        self.assertIsNotNone(importlib.util.find_spec('paperdrop.distribution'),
                             'Homebrew distribution detection is not implemented')
        return importlib.import_module('paperdrop.distribution')

    def marked(self, prefix):
        self.marker.write_text('OPT_PREFIX = %r\n' % str(prefix), encoding='utf-8')
        return patch('paperdrop.distribution.__file__', str(self.package / 'distribution.py'))


class MarkerTests(HomebrewFixture):
    def test_reads_package_relative_marker_without_resolving_stable_opt_path(self):
        distribution = self.distribution()
        for value in ('/opt/homebrew/opt/paperdrop', '/usr/local/opt/paperdrop',
                      '/home/linuxbrew/.linuxbrew/opt/paperdrop'):
            with self.subTest(value=value), self.marked(value):
                self.assertEqual(distribution.homebrew_opt_prefix(), Path(value))

    def test_invalid_or_executable_markers_are_unknown_and_never_executed(self):
        distribution = self.distribution()
        touched = self.root / 'executed'
        values = (None, 42, True, [], '', 'opt/paperdrop', '/usr/local/paperdrop',
                  '/usr/local/opt/other', '/usr/local/Cellar/paperdrop/1/opt/paperdrop',
                  '/usr/local/../local/opt/paperdrop', '/usr/local/./opt/paperdrop',
                  '/usr/local//opt/paperdrop', '//usr/local/opt/paperdrop',
                  '/usr/local/opt/paperdrop/', '/bad:prefix/opt/paperdrop',
                  '/bad\nroot/opt/paperdrop', '/bad\x00root/opt/paperdrop')
        sources = ['OPT_PREFIX = %r\n' % value for value in values]
        sources += ['', '{', 'OTHER = "/usr/local/opt/paperdrop"',
                    'OPT_PREFIX = "/usr/local/opt/paperdrop"\nOTHER = 1',
                    'OPT_PREFIX = OTHER = "/usr/local/opt/paperdrop"',
                    'OPT_PREFIX = "/usr/local/opt/" + "paperdrop"',
                    'OPT_PREFIX: str = "/usr/local/opt/paperdrop"',
                    'OPT_PREFIX = "/usr/local/opt/paperdrop"\n' * 2,
                    'OPT_PREFIX = "/usr/local/opt/paperdrop"\n' +
                    'open(%r, "w").write("bad")\n' % str(touched),
                    '#' + 'x' * 4096 + '\nOPT_PREFIX = "/usr/local/opt/paperdrop"']
        with patch.object(distribution, '__file__', str(self.package / 'distribution.py')):
            for source in sources:
                self.marker.write_text(source, encoding='utf-8')
                with self.subTest(source=source[:100]):
                    self.assertIsNone(distribution.homebrew_opt_prefix())
        self.assertFalse(touched.exists())

    def test_absent_foreign_or_non_regular_marker_is_not_homebrew(self):
        distribution = self.distribution()
        with patch.dict(os.environ, {'HOMEBREW_PREFIX': '/usr/local',
                                     'HOMEBREW_CELLAR': '/usr/local/Cellar'}), \
                patch.object(distribution, '__file__', str(self.package / 'distribution.py')):
            self.assertIsNone(distribution.homebrew_opt_prefix())
            foreign = self.root / '_homebrew.py'
            foreign.write_text('OPT_PREFIX = "/usr/local/opt/paperdrop"\n')
            self.assertIsNone(distribution.homebrew_opt_prefix())
            self.marker.symlink_to(foreign)
            self.assertIsNone(distribution.homebrew_opt_prefix())
            self.marker.unlink()
            self.marker.mkdir()
            self.assertIsNone(distribution.homebrew_opt_prefix())

    def test_source_package_has_no_generated_homebrew_marker(self):
        import paperdrop
        self.assertFalse(Path(paperdrop.__file__).with_name('_homebrew.py').exists())


class UpgradeOwnershipTests(HomebrewFixture):
    def test_brew_upgrade_is_refused_before_installation_network_or_subprocess(self):
        from paperdrop import upgrade
        self.distribution()
        with self.marked('/opt/homebrew/opt/paperdrop'), \
                patch.object(upgrade, '_installation', side_effect=upgrade.UpgradeError('Homebrew must not inspect pipx')) as installation, \
                patch.object(upgrade, '_download') as download, \
                patch.object(upgrade.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(upgrade.UpgradeError,
                                        'brew upgrade pelikanm400/tap/paperdrop'):
                upgrade.upgrade_software()
        installation.assert_not_called()
        download.assert_not_called()
        popen.assert_not_called()

    def test_invalid_marker_keeps_unknown_installation_fail_closed(self):
        from paperdrop import upgrade
        self.distribution()
        with self.marked('/usr/local/Cellar/paperdrop/0.3.0'), \
                patch.object(upgrade, '_download') as download, \
                patch.object(upgrade, '_run_command') as run:
            with self.assertRaisesRegex(upgrade.UpgradeError, 'canonical.*pipx'):
                upgrade.upgrade_software()
        download.assert_not_called()
        run.assert_not_called()

    def test_brew_cli_error_preserves_json_and_never_opens_ui(self):
        import contextlib
        import io
        import json
        from paperdrop import cli, upgrade
        self.distribution()
        for arguments in (['upgrade', '--json'], ['--json', 'upgrade', '--no-ui'],
                          ['upgrade', '--no-ui'], ['upgrade']):
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(arguments=arguments), self.marked('/usr/local/opt/paperdrop'), \
                    patch.object(upgrade, '_installation', side_effect=upgrade.UpgradeError('Homebrew must not inspect pipx')) as installation, \
                    patch.object(upgrade, '_download') as download, \
                    patch.object(cli.subprocess, 'Popen') as popen, \
                    patch.object(cli.subprocess, 'call') as call, \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(cli.main(arguments), 1)
            if '--json' in arguments:
                payload = json.loads(stdout.getvalue())
                self.assertEqual(set(payload), {'ok', 'error'})
                self.assertIs(payload['ok'], False)
                self.assertIn('brew upgrade pelikanm400/tap/paperdrop', payload['error'])
                self.assertEqual(stderr.getvalue(), '')
            else:
                self.assertEqual(stdout.getvalue(), '')
                self.assertIn('brew upgrade pelikanm400/tap/paperdrop', stderr.getvalue())
            installation.assert_not_called()
            download.assert_not_called()
            popen.assert_not_called()
            call.assert_not_called()


class RuntimeTests(HomebrewFixture):
    def test_homebrew_scheduler_uses_only_stable_opt_paths_and_isolated_python(self):
        from paperdrop.platforms.base import runtime_command
        self.distribution()
        for prefix in ('/opt/homebrew', '/usr/local', '/home/linuxbrew/.linuxbrew'):
            opt = Path(prefix) / 'opt' / 'paperdrop'
            with self.subTest(prefix=prefix), self.marked(opt), \
                    patch.dict(os.environ, {'PATH': '/old/pipx/bin', 'PYTHONPATH': '/old/checkout',
                                             'HOMEBREW_PREFIX': '/fake'}):
                argv, env = runtime_command()
            self.assertEqual(argv, [str(opt / 'libexec/bin/python'), '-I', '-m', 'paperdrop'])
            self.assertEqual(env, {'PATH': '%s/bin:%s/bin:/usr/bin:/bin:/usr/sbin:/sbin'
                                          % (opt, prefix)})
            self.assertNotIn('Cellar', repr((argv, env)))
            self.assertNotIn('PYTHONPATH', env)

    def test_non_brew_scheduler_retains_interpreter_and_source_import_path(self):
        import sys
        from paperdrop.platforms import base
        self.distribution()
        with patch('paperdrop.distribution.__file__', str(self.package / 'distribution.py')), \
                patch.dict(os.environ, {'HOMEBREW_PREFIX': '/opt/homebrew'}):
            argv, env = base.runtime_command()
        self.assertEqual(argv, [sys.executable, '-m', 'paperdrop'])
        self.assertEqual(env, {'PYTHONPATH': str(Path(base.__file__).resolve().parents[2])})


class MacHelperTests(HomebrewFixture):
    def setUp(self):
        super().setUp()
        self.distribution()
        self.opt = self.root / 'homebrew' / 'opt' / 'paperdrop'
        self.brew_helper = self.opt / 'bin' / 'paperdrop-set'
        self.brew_helper.parent.mkdir(parents=True)
        self.brew_helper.write_text('#!/bin/sh\nexit 0\n')
        self.brew_helper.chmod(0o700)
        self.old_helper = self.root / 'old-pipx-helper'
        self.old_helper.write_text('#!/bin/sh\nexit 99\n')
        self.old_helper.chmod(0o700)

    def test_brew_helper_precedes_stale_path_and_legacy_user_helper(self):
        from paperdrop.platforms.macos import MacOS
        fallback = self.root / 'Library/Application Support/PaperDrop/paperdrop-set'
        fallback.parent.mkdir(parents=True)
        fallback.write_text('old helper')
        with self.marked(self.opt), \
                patch('paperdrop.platforms.macos.shutil.which', return_value=str(self.old_helper)) as which:
            self.assertEqual(MacOS(home=self.root).helper(), str(self.brew_helper))
        which.assert_not_called()

    def test_missing_brew_helper_is_an_error_not_a_fallback_to_old_pipx(self):
        from paperdrop.errors import PlatformError
        from paperdrop.platforms.macos import MacOS
        self.brew_helper.unlink()
        with self.marked(self.opt), \
                patch('paperdrop.platforms.macos.shutil.which', return_value=str(self.old_helper)) as which:
            with self.assertRaisesRegex(PlatformError, 'Homebrew.*paperdrop-set'):
                MacOS(home=self.root).helper()
        which.assert_not_called()

    def test_explicit_helper_overrides_brew_and_missing_explicit_helper_does_not_fall_back(self):
        from paperdrop.errors import PlatformError
        from paperdrop.platforms.macos import MacOS
        config = {'apply': {'helper_path': str(self.old_helper)}}
        with self.marked(self.opt):
            self.assertEqual(MacOS(config=config, home=self.root).helper(), str(self.old_helper))
            self.old_helper.unlink()
            with self.assertRaisesRegex(PlatformError, 'apply.helper_path'):
                MacOS(config=config, home=self.root).helper()

    def test_non_brew_keeps_path_then_user_helper_fallback(self):
        from paperdrop.platforms.macos import MacOS
        fallback = self.root / 'Library/Application Support/PaperDrop/paperdrop-set'
        fallback.parent.mkdir(parents=True)
        fallback.write_text('old helper')
        with patch('paperdrop.distribution.__file__', str(self.package / 'distribution.py')):
            with patch('paperdrop.platforms.macos.shutil.which', return_value=str(self.old_helper)):
                self.assertEqual(MacOS(home=self.root).helper(), str(self.old_helper))
            with patch('paperdrop.platforms.macos.shutil.which', return_value=None):
                self.assertEqual(MacOS(home=self.root).helper(), str(fallback))


class PipxCompatibilityTests(pipx_tests.PipxFixture):
    def test_environment_or_invalid_marker_does_not_reclassify_canonical_pipx(self):
        from paperdrop import distribution
        for source in (None, 'OPT_PREFIX = "/not-an-opt-prefix"\n'):
            marker = self.module.with_name('_homebrew.py')
            if source is None:
                marker.unlink(missing_ok=True)
            else:
                marker.write_text(source)
            with self.subTest(source=source), \
                    patch.object(distribution, '__file__', str(self.module.with_name('distribution.py'))), \
                    patch.dict(os.environ, {'HOMEBREW_PREFIX': '/opt/homebrew'}), \
                    patch.object(self.u, '_download', return_value=b'[]') as download, \
                    patch.object(self.u, '_run_command') as run:
                self.assertEqual(self.u.upgrade_software(), {
                    'ok': True, 'changed': False, 'version': self.u.__version__,
                    'python': str(self.python)})
            download.assert_called_once_with(self.u.RELEASES_URL, self.u.MAX_RELEASE_BYTES, 'json')
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
