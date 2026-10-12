"""Platform adapters, driven through fake command runners.

PaperDrop cannot run macOS or Xfce here, so these tests check exactly what can
be checked off-device: the scheduler artifacts the installers write, and the
commands the Xfce adapter builds from discovered properties.
"""
from __future__ import annotations

import json
import os
import plistlib
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import support

from paperdrop import __version__
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

    def test_desktop_state_reports_every_screen_and_its_wallpaper(self):
        """The helper's `screens` output is the only per-display source on macOS.

        Reading the main screen alone is what let a second display keep an older
        image while the same-day check reported the desktop as up to date.
        """
        lines = ('0\tBuilt-in Retina Display\t1728x1117\tscale 2.0\t/cache/image-aaa.jpg\n'
                 '1\tLG UltraFine\t1920x1080\tscale 2.0\t/cache/current.jpg\n'
                 '2\tNo wallpaper yet\t1920x1080\tscale 1.0\t\n'
                 'malformed line without tabs\n')
        self.runner.stdout_for = lambda argv: lines
        platform, _ = self.build()
        with patch.object(platform, 'helper', return_value='/fake/paperdrop-set'):
            state = platform.desktop_state()
        self.assertEqual(state, [
            ('screen 0 Built-in Retina Display', '/cache/image-aaa.jpg'),
            ('screen 1 LG UltraFine', '/cache/current.jpg'),
            ('screen 2 No wallpaper yet', None)])

    def test_resident_watch_passes_cache_directory_not_startup_image(self):
        from paperdrop.cache import Cache
        cache = Cache(self.home / 'cache')
        first = cache.publish(b'A', '.jpg')
        platform, paths = self.build()
        with patch.object(platform, 'helper', return_value='/fake/paperdrop-set'), \
                patch('paperdrop.platforms.macos.subprocess.call', return_value=0) as call:
            self.assertEqual(platform.watch(str(cache.directory), lock_file=paths.lock_file), 0)
        self.assertEqual(call.call_args[0][0],
                         ['/fake/paperdrop-set', 'watch-cache', str(cache.directory),
                          '--lock-file', str(paths.lock_file)])
        cache.publish(b'B', '.jpg')
        self.assertNotEqual(cache.current(), first)

    def test_watch_source_resolves_current_inside_each_event_callback(self):
        # Source contract only: no Swift/AppKit compiler or runtime on Linux.
        source = (support.ROOT / 'macos/Sources/paperdrop-set/main.swift').read_text()
        self.assertIn('case "watch-cache":', source)
        self.assertNotIn('try watch({ path }', source,
                         'the legacy watch command must also pick up a migrated index')
        self.assertIn('try watch({ try cachedImagePath(directory) }', source)
        body = source.split('func watch(', 1)[1].split('func run()', 1)[0]
        self.assertIn('try applyImage(resolve(), fill: fill)', body)
        callback = body.split('let reapply:', 1)[1]
        self.assertIn('try applyLatest()', callback)
        self.assertIn('NotificationCenter.default.addObserver', body)
        self.assertIn('NSWorkspace.didWakeNotification', body)

    def test_native_event_lock_protocol_covers_resolve_apply_not_resident_lifetime(self):
        # Native source contract plus test_pipeline's deterministic POSIX interleaving;
        # neither is macOS compilation or real AppKit execution.
        source = (support.ROOT / 'macos/Sources/paperdrop-set/main.swift').read_text()
        self.assertIn('func withUpdateLock(', source)
        lock = source.split('func withUpdateLock(', 1)[1].split('func watch(', 1)[0]
        self.assertIn('while flock(fd, LOCK_EX) != 0', lock)
        self.assertIn('flock(fd, LOCK_UN)', lock)
        self.assertIn('defer', lock)
        watch = source.split('func watch(', 1)[1].split('func run()', 1)[0]
        self.assertIn('withUpdateLock(lockPath)', watch)
        self.assertIn('try applyImage(resolve(), fill: fill)', watch)
        self.assertIn('try applyLatest()', watch.split('let reapply:', 1)[1])
        self.assertIn('RunLoop.main.run()', watch)
        self.assertIn('arguments.firstIndex(of: "--lock-file")', source)
        apply = source.split('case "apply":', 1)[1].split('case "current":', 1)[0]
        self.assertNotIn('withUpdateLock', apply, 'Python already holds the lock during helper apply')

    def test_pause_parks_the_plists_so_it_survives_a_restart(self):
        """`launchctl bootout` alone only lasts until the next login.

        launchd loads every plist in ~/Library/LaunchAgents when the user logs in,
        so a pause that only unloads looks resumed after a restart. Parking the
        files outside that directory is what makes the pause real.
        """
        platform, paths = self.build()
        with patch('paperdrop.platforms.macos.shutil.which', return_value='/usr/bin/launchctl'):
            platform.install(paths, '07:35')
            self.assertTrue((self.agents() / (APPLY_LABEL + '.plist')).exists())
            message = platform.pause()
            self.assertIn('restart', message)
            self.assertFalse((self.agents() / (APPLY_LABEL + '.plist')).exists())
            self.assertFalse((self.agents() / (WATCH_LABEL + '.plist')).exists())
            parked = platform._parked_path(APPLY_LABEL)
            self.assertTrue(parked.exists(), 'the plist is kept, never deleted')
            self.assertNotIn(str(self.agents()), str(parked),
                             'parked outside the directory launchd auto-loads')
            self.assertIn('agent %s: paused' % APPLY_LABEL, '\n'.join(platform.status_lines()))
            self.assertIn('paused', str(platform.doctor()))
            platform.resume()
        self.assertTrue((self.agents() / (APPLY_LABEL + '.plist')).exists())
        self.assertFalse(platform._parked_path(APPLY_LABEL).exists())
        self.assertIn('agent %s: installed' % APPLY_LABEL, '\n'.join(platform.status_lines()))

    def test_uninstall_removes_parked_plists_too(self):
        """A parked plist left behind would come back on the next `resume`."""
        platform, paths = self.build()
        with patch('paperdrop.platforms.macos.shutil.which', return_value='/usr/bin/launchctl'):
            platform.install(paths, '07:35')
            platform.pause()
            removed = platform.uninstall()
        self.assertFalse(platform._parked_path(APPLY_LABEL).exists())
        self.assertEqual(len(removed), 2)

    def test_login_catch_up_can_be_switched_off(self):
        platform, paths = self.build(schedule={'catch_up_on_login': False})
        with patch('paperdrop.platforms.macos.shutil.which', return_value='/usr/bin/launchctl'):
            platform.install(paths, '07:35')
        payload = plistlib.loads((self.agents() / (APPLY_LABEL + '.plist')).read_bytes())
        self.assertNotIn('RunAtLoad', payload)
        self.assertEqual(payload['StartCalendarInterval'], {'Hour': 7, 'Minute': 35})
        self.assertEqual(payload['StartInterval'], 3600,
                         'the hourly catch-up still covers the missed day')

    def test_turning_screen_watching_off_removes_the_installed_watcher(self):
        platform, paths = self.build()
        with patch('paperdrop.platforms.macos.shutil.which', return_value='/usr/bin/launchctl'):
            platform.install(paths, '07:35')
            self.assertTrue((self.agents() / (WATCH_LABEL + '.plist')).exists())
            off = MacOS(config=validate({'source': {'url': 'https://example.test/desk.json'},
                                         'schedule': {'watch_screen_changes': False}}),
                        home=self.home, runner=self.runner, environ={'HOME': str(self.home)})
            created = off.install(paths, '07:35')
        self.assertFalse((self.agents() / (WATCH_LABEL + '.plist')).exists())
        self.assertEqual(len(created), 1)
        self.assertEqual(off.agent_state(WATCH_LABEL), 'disabled')
        self.assertEqual(off.agent_state(APPLY_LABEL), 'installed')
        self.assertIn('disabled', '\n'.join(off.status_lines()))

    def test_watch_passes_custom_config_lock_path_to_native_helper(self):
        import inspect
        from paperdrop.cache import Cache
        platform, paths = self.build(state_file=str(self.home / 'custom-state' / 'run.json'))
        self.assertIn('lock_file', inspect.signature(platform.watch).parameters)
        image = Cache(self.home / 'cache').publish(b'A', '.jpg')
        with patch.object(platform, 'helper', return_value='/fake/paperdrop-set'), \
                patch('paperdrop.platforms.macos.subprocess.call', return_value=0) as call:
            self.assertEqual(platform.watch(str(image.parent), lock_file=paths.lock_file), 0)
        self.assertEqual(call.call_args[0][0], ['/fake/paperdrop-set', 'watch-cache',
                         str(image.parent), '--lock-file', str(paths.lock_file)])

    def test_the_native_helper_calls_flock_unqualified(self):
        """`Darwin.flock(...)` resolves to `struct flock`, not to the syscall.

        Module Darwin exports both the C function and the struct, so qualifying
        the call made swiftc 6.4 report "argument passed to call that takes no
        arguments" and the helper never built on a real Mac. The unqualified form
        is the one that compiles; keep it that way.
        """
        source = (support.ROOT / 'macos/Sources/paperdrop-set/main.swift').read_text()
        self.assertNotIn('Darwin.flock(', source)
        self.assertIn('while flock(fd, LOCK_EX) != 0', source)
        self.assertIn('flock(fd, LOCK_UN)', source)

    def test_native_index_validates_regular_roles_and_selects_confirmed_active(self):
        # Source contract only; compilation/AppKit acceptance remains pending.
        source = (support.ROOT / 'macos/Sources/paperdrop-set/main.swift').read_text()
        self.assertIn('func checkedImagePath(', source)
        self.assertIn('attributesOfItem(atPath:', source)
        self.assertIn('FileAttributeType.typeRegular', source)
        self.assertIn('container.contains(.active)', source,
                      'explicit null active must not fall back to an unconfirmed candidate')
        resolver = source.split('func cachedImagePath(', 1)[1].split('func watch(', 1)[0]
        self.assertIn('[index.current, index.previous, index.active]', resolver)
        self.assertIn('checkedImagePath(', resolver)
        self.assertIn('let name = index.active', resolver)

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

    def test_capping_shrinks_the_launchd_logs_but_only_when_needed(self):
        # launchd owns these two files and never rotates them, so PaperDrop has to.
        platform, _ = self.build()
        logs = pathlib.Path(self.home) / 'Library' / 'Logs'
        logs.mkdir(parents=True)
        big = logs / (APPLY_LABEL + '.out.log')
        small = logs / (APPLY_LABEL + '.err.log')
        big.write_bytes(b'a line of output\n' * 4000)
        small.write_bytes(b'one short line\n')

        shrunk = platform.cap_own_logs(4096)
        self.assertEqual([path for path, _ in shrunk], [str(big)])
        self.assertLessEqual(big.stat().st_size, 4096 + 100)
        self.assertEqual(small.read_bytes(), b'one short line\n', 'a small file is untouched')

    def test_capping_ignores_logs_that_were_never_written(self):
        platform, _ = self.build()
        self.assertEqual(platform.cap_own_logs(4096), [])

    def test_the_command_line_written_for_launchd_is_one_the_cli_accepts(self):
        # The bug this exists for: the plist used to say "run --config X", but
        # --config is a global option, so argparse rejected it and launchd
        # recorded exit 2 on every fire. Nothing ran, and nothing was logged
        # about it beyond the usage error. Asserting that "--config" is merely
        # present was not enough; the generated argv has to be run.
        platform, paths = self.build()
        paths.config_file.write_text(json.dumps({
            'source': {'url': 'https://127.0.0.1:9/desk.json'},   # refused instantly
            'cache_dir': str(pathlib.Path(self.home) / 'cache'),
            'state_file': str(pathlib.Path(self.home) / 'state.json'),
            'log': {'path': str(pathlib.Path(self.home) / 'log')},
        }))
        with patch('paperdrop.platforms.macos.shutil.which', return_value='/usr/bin/launchctl'):
            platform.install(paths, '05:05')
        payload = plistlib.loads((self.agents() / (APPLY_LABEL + '.plist')).read_bytes())
        argv = payload['ProgramArguments']
        self.assertLess(argv.index('--config'), argv.index('run'),
                        '--config must come before the verb')

        env = dict(os.environ)
        env.update(payload.get('EnvironmentVariables') or {})
        completed = subprocess.run(argv, capture_output=True, env=env, timeout=180)
        self.assertNotEqual(completed.returncode, 2,
                            'launchd would exit 2 again: %s'
                            % completed.stderr.decode()[-400:])
        self.assertNotIn(b'unrecognized arguments', completed.stderr)

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

    def test_desktop_state_reports_every_monitor_and_workspace(self):
        platform, _ = self.build()
        state = platform.desktop_state()
        self.assertEqual(len(state), 3, 'both monitors and both workspaces')
        for prop, value in state:
            self.assertTrue(prop.startswith('/backdrop/') and prop.endswith('/last-image'))
            self.assertEqual(value, '/old/wall.jpg')

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

    def test_a_missed_run_is_persisted_only_when_login_catch_up_is_on(self):
        """systemd's Persistent= is the Linux counterpart of the login catch-up."""
        for flag, expected in ((False, 'Persistent=false'), (True, 'Persistent=true')):
            config = validate({'source': {'url': 'https://example.test/desk.json'},
                               'schedule': {'catch_up_on_login': flag}})
            platform = Xfce(config=config, runner=self.runner, home=self.home,
                            environ={'HOME': str(self.home), 'DISPLAY': ':0',
                                     'XDG_CONFIG_HOME': str(self.home / '.config')},
                            binary='/usr/bin/xfconf-query', systemctl='/usr/bin/systemctl')
            paths = Paths.from_config(config, config_file=self.home / 'config.json')
            _, timer = platform._unit_text(paths, '05:05')
            self.assertIn(expected, timer)

    def test_the_exec_start_line_puts_the_global_option_first(self):
        # Same failure mode as the launchd plist: "run --config X" is a usage
        # error, so the timer would fire and the unit would exit 2 silently.
        platform, paths = self.build()
        service, _ = platform._unit_text(paths, '05:05')
        exec_line = [line for line in service.splitlines() if line.startswith('ExecStart=')][0]
        self.assertIn('--config', exec_line)
        self.assertLess(exec_line.index('--config'), exec_line.index(' run'),
                        'the global option must precede the verb')

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
        self.assertIn(__version__, str(caught.exception),
                      'the message must name the version that is actually running')


if __name__ == '__main__':
    unittest.main(verbosity=2)
