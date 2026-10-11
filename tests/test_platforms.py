"""Platform adapters, driven through fake command runners.

PaperDrop cannot run macOS or Xfce here, so these tests check exactly what can
be checked off-device: the scheduler artifacts the installers write, and the
commands the Xfce adapter builds from discovered properties.
"""
from __future__ import annotations

import plistlib
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import support

from paperdrop.config import validate
from paperdrop.errors import PlatformError
from paperdrop.paths import Paths
from paperdrop.platforms.macos import APPLY_LABEL, WATCH_LABEL, MacOS
from paperdrop.platforms.xfce import CHANNEL, Xfce, xfconf_available

BACKDROP = [
    '/backdrop/screen0/monitorDP-1/workspace0/last-image',
    '/backdrop/screen0/monitorDP-1/workspace1/last-image',
    '/backdrop/screen0/monitoreDP-1/workspace0/last-image',
    '/backdrop/screen0/monitorDP-1/workspace0/image-style',
    '/desktop-icons/show-tooltips',
]


def completed(argv, stdout='', code=0):
    return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr='')


class FakeRunner:
    def __init__(self, stdout_for=None):
        self.calls = []
        self.stdout_for = stdout_for or (lambda argv: '')

    def __call__(self, argv, env=None):
        self.calls.append(list(argv))
        return completed(argv, stdout=self.stdout_for(argv))

    def argv_for(self, needle):
        """Calls where any argument contains `needle` (not just equals it)."""
        return [call for call in self.calls if any(needle in part for part in call)]


class MacPlannerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = pathlib.Path(self.directory.name)
        self.runner = FakeRunner()
        self.config = validate({'source': {'url': 'https://example.test/desk.json'}})
        self.paths = Paths.from_config(self.config, config_file=self.home / 'config.json')

    def build(self, **overrides):
        config = validate({'source': {'url': 'https://example.test/desk.json'},
                           **overrides})
        platform = MacOS(config=config, home=self.home, runner=self.runner, environ={'HOME': str(self.home)})
        return platform, Paths.from_config(config, config_file=self.home / 'config.json')

    def agents(self):
        return pathlib.Path(self.home) / 'Library' / 'LaunchAgents'

    def test_install_writes_both_agents_with_daily_trigger_and_catch_up(self):
        platform, paths = self.build()
        with patch('paperdrop.platforms.macos.shutil.which', return_value='/usr/bin/launchctl'):
            created = platform.install(paths, '05:05')
        self.assertEqual(len(created), 2)
        apply_plist = plistlib.loads((self.agents() / (APPLY_LABEL + '.plist')).read_bytes())
        self.assertEqual(apply_plist['Label'], APPLY_LABEL)
        self.assertEqual(apply_plist['StartCalendarInterval'], {'Hour': 5, 'Minute': 5})
        self.assertIs(apply_plist['RunAtLoad'], True)
        self.assertEqual(apply_plist['StartInterval'], 3600)
        self.assertEqual(apply_plist['LimitLoadToSessionType'], 'Aqua')
        self.assertIn('run', apply_plist['ProgramArguments'])
        self.assertIn('--config', apply_plist['ProgramArguments'])

        watch_plist = plistlib.loads((self.agents() / (WATCH_LABEL + '.plist')).read_bytes())
        self.assertIs(watch_plist['KeepAlive'], True)
        self.assertIn('watch', watch_plist['ProgramArguments'])

    def test_catch_up_interval_can_be_disabled(self):
        platform, paths = self.build(schedule={'daily_time': '05:05',
                                               'catch_up_interval_minutes': 0})
        with patch('paperdrop.platforms.macos.shutil.which', return_value='/usr/bin/launchctl'):
            platform.install(paths, '05:05')
        apply_plist = plistlib.loads((self.agents() / (APPLY_LABEL + '.plist')).read_bytes())
        self.assertNotIn('StartInterval', apply_plist)
        self.assertIs(apply_plist['RunAtLoad'], True, 'login catch-up stays')

    def test_catch_up_interval_is_configurable(self):
        platform, paths = self.build(schedule={'daily_time': '06:30',
                                               'catch_up_interval_minutes': 15})
        with patch('paperdrop.platforms.macos.shutil.which', return_value='/usr/bin/launchctl'):
            platform.install(paths, '06:30')
        apply_plist = plistlib.loads((self.agents() / (APPLY_LABEL + '.plist')).read_bytes())
        self.assertEqual(apply_plist['StartCalendarInterval'], {'Hour': 6, 'Minute': 30})
        self.assertEqual(apply_plist['StartInterval'], 900)

    def test_uninstall_removes_both_agents(self):
        platform, paths = self.build()
        with patch('paperdrop.platforms.macos.shutil.which', return_value='/usr/bin/launchctl'):
            platform.install(paths, '05:05')
            removed = platform.uninstall()
        self.assertEqual(len(removed), 2)
        self.assertEqual(list(self.agents().iterdir()), [])

    def test_a_missing_helper_is_a_clear_error(self):
        platform, _ = self.build()
        image = pathlib.Path(self.home) / 'wall.jpg'
        image.write_bytes(b'x')
        with patch('paperdrop.platforms.macos.shutil.which', return_value=None):
            with self.assertRaises(PlatformError) as caught:
                platform.apply(str(image))
        self.assertIn('build.sh', str(caught.exception))

    def test_apply_refuses_a_missing_file(self):
        platform, _ = self.build()
        with patch.object(MacOS, 'helper', return_value='/usr/bin/true'):
            with self.assertRaises(PlatformError) as caught:
                platform.apply('/nope/wall.jpg')
        self.assertIn('missing', str(caught.exception))

    def test_apply_passes_fill_to_the_helper_for_every_screen(self):
        image = pathlib.Path(self.home) / 'wall.jpg'
        image.write_bytes(b'x')
        platform, _ = self.build()
        with patch.object(MacOS, 'helper', return_value='/usr/bin/true'):
            platform.apply(str(image))
        call = self.runner.argv_for('true')[0]
        self.assertIn('apply', call)
        self.assertIn('--all-screens', call)
        self.assertIn('--fill', call)
        self.assertIn(str(image), call)


class XfceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = pathlib.Path(self.directory.name)
        self.values = {prop: '/old/wall.jpg' for prop in BACKDROP if prop.endswith('last-image')}
        self.set_calls = []

        def stdout_for(argv):
            if '-l' in argv:
                return '\n'.join(BACKDROP) + '\n'
            if '-p' in argv:
                prop = argv[argv.index('-p') + 1]
                if '-s' in argv:
                    self.set_calls.append((prop, argv[argv.index('-s') + 1], list(argv)))
                    return ''
                return self.values.get(prop, '') + '\n'
            return ''

        self.runner = FakeRunner(stdout_for)

    def build(self, **apply_config):
        config = validate({'source': {'url': 'https://example.test/desk.json'},
                           'apply': apply_config or {}})
        platform = Xfce(config=config, runner=self.runner, home=self.home,
                        environ={'HOME': str(self.home), 'DISPLAY': ':0',
                                 'XDG_CONFIG_HOME': str(self.home / '.config')},
                        binary='/usr/bin/xfconf-query', systemctl='/usr/bin/systemctl')
        return platform, Paths.from_config(config, config_file=self.home / 'config.json')

    def test_only_backdrop_image_properties_are_discovered(self):
        platform, _ = self.build()
        self.assertEqual(sorted(platform.image_properties()), sorted(
            prop for prop in BACKDROP if prop.startswith('/backdrop/')
            and prop.endswith('/last-image')))
        self.assertNotIn('/backdrop/screen0/monitorDP-1/workspace0/image-style',
                         platform.image_properties())
        self.assertNotIn('/desktop-icons/show-tooltips', platform.image_properties())

    def test_apply_sets_every_monitor_and_workspace_image(self):
        platform, _ = self.build()
        platform.apply('/cache/current.jpg')
        images = [call for call in self.set_calls if call[0].endswith('/last-image')]
        self.assertEqual(len(images), 3, 'both monitors and both workspaces')
        for _, value, _ in images:
            self.assertEqual(value, '/cache/current.jpg')

    def test_apply_leaves_the_background_style_alone_by_default(self):
        platform, _ = self.build()
        platform.apply('/cache/current.jpg')
        self.assertEqual([call for call in self.set_calls if call[0].endswith('image-style')], [])

    def test_apply_can_manage_the_style_when_configured(self):
        platform, _ = self.build(**{'xfce_image_style': 5})
        platform.apply('/cache/current.jpg')
        styles = [call for call in self.set_calls if call[0].endswith('image-style')]
        self.assertEqual(len(styles), 3)
        for _, value, argv in styles:
            self.assertEqual(value, '5')
            self.assertIn('-n', argv, 'the property is created if it does not exist')
            self.assertIn('-t', argv)

    def test_current_reports_the_shared_wallpaper(self):
        platform, _ = self.build()
        self.assertEqual(platform.current(), '/old/wall.jpg')
        self.assertEqual(len(platform.current_all()), 3)

    def test_a_missing_session_is_a_helpful_error(self):
        config = validate({'source': {'url': 'https://example.test/desk.json'}})
        platform = Xfce(config=config, runner=self.runner, home=self.home,
                        environ={'HOME': str(self.home)}, binary='/usr/bin/xfconf-query')
        with self.assertRaises(PlatformError) as caught:
            platform.current()
        self.assertIn('graphical session', str(caught.exception))

    def test_unit_text_matches_the_configured_time(self):
        platform, paths = self.build()
        service, timer = platform._unit_text(paths, '04:30')
        self.assertIn('OnCalendar=*-*-* 04:30:00', timer)
        self.assertIn('Persistent=true', timer)
        self.assertIn('WantedBy=timers.target', timer)
        self.assertIn('ExecStart=', service)
        self.assertIn('run', service)
        self.assertIn('Type=oneshot', service)

    def test_install_writes_the_units_and_enables_the_timer(self):
        platform, paths = self.build()
        created = platform.install(paths, '05:05')
        self.assertEqual(len(created), 2)
        units = self.home / '.config' / 'systemd' / 'user'
        self.assertIn('05:05', (units / 'paperdrop.timer').read_text())
        systemctl_calls = [call for call in self.runner.calls if 'systemctl' in call[0]]
        self.assertTrue(any('enable' in call for call in systemctl_calls))

    def test_uninstall_disables_and_removes_the_units(self):
        platform, paths = self.build()
        platform.install(paths, '05:05')
        removed = platform.uninstall()
        self.assertEqual(len(removed), 2)
        units = self.home / '.config' / 'systemd' / 'user'
        self.assertEqual(list(units.iterdir()), [])

    def test_xfconf_availability_detection(self):
        self.assertTrue(xfconf_available(which=lambda name: '/usr/bin/' + name))
        self.assertFalse(xfconf_available(which=lambda name: None))


class DetectionTests(unittest.TestCase):
    def test_xfce_is_used_when_xfconf_query_exists(self):
        from paperdrop.platforms import detect_platform
        config = validate({'source': {'url': 'https://example.test/desk.json'}})
        platform = detect_platform(config, environ={'DISPLAY': ':0', 'HOME': '/tmp'},
                                   which=lambda name: '/usr/bin/' + name)
        self.assertEqual(platform.name, 'xfce')

    def test_a_missing_desktop_is_reported_clearly(self):
        from paperdrop.platforms import detect_platform
        config = validate({'source': {'url': 'https://example.test/desk.json'}})
        with self.assertRaises(PlatformError) as caught:
            detect_platform(config, environ={}, which=lambda name: None)
        self.assertIn('macOS and Xfce', str(caught.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
