"""Xfce adapter: drive xfconf-query on the xfce4-desktop channel.

The property paths are discovered at run time rather than hardcoded: they embed
the monitor name and workspace index, so they change with the hardware.

The background *style* (how the image is scaled) is deliberately preserved by
default: PaperDrop only replaces the image, so whatever "fill the screen without
stretching" style you configured in Xfce keeps applying.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

from ..errors import PlatformError
from .base import Platform, runtime_command

CHANNEL = 'xfce4-desktop'
BACKDROP_PREFIX = '/backdrop/'
IMAGE_SUFFIX = '/last-image'
STYLE_SUFFIX = '/image-style'

SERVICE_NAME = 'paperdrop.service'
TIMER_NAME = 'paperdrop.timer'


def xfconf_available(which=None):
    finder = which or shutil.which
    return bool(finder('xfconf-query'))


def _default_runner(argv, env=None):
    return subprocess.run(argv, capture_output=True, text=True, env=env)


class Xfce(Platform):
    name = 'xfce'
    supports_screen_watch = False

    def __init__(self, config=None, environ=None, runner=None, home=None,
                 binary=None, systemctl=None):
        super().__init__(config)
        self.environ = dict(os.environ if environ is None else environ)
        self._runner = runner or _default_runner
        self.home = pathlib.Path(home or self.environ.get('HOME') or pathlib.Path.home())
        # Resolved lazily, but injectable so the adapter can be exercised without
        # Xfce installed.
        self._binary = binary
        self._systemctl_binary = systemctl
        apply_config = (config or {}).get('apply', {}) or {}
        self.image_style = apply_config.get('xfce_image_style')

    # --- session ------------------------------------------------------------
    def session_env(self):
        env = dict(self.environ)
        if not env.get('DISPLAY') and not env.get('WAYLAND_DISPLAY'):
            raise PlatformError(
                'No graphical session found (DISPLAY is unset). PaperDrop must run inside '
                'your desktop session: use the installed systemd --user timer or a session '
                'autostart entry, not a plain SSH shell or a root cron job.')
        return env

    def _xfconf(self, *args):
        binary = self._binary or shutil.which('xfconf-query')
        if not binary:
            raise PlatformError('xfconf-query was not found on PATH.')
        result = self._runner([binary, *args], env=self.session_env())
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            raise PlatformError('xfconf-query %s failed: %s' % (' '.join(args), detail))
        return result

    # --- discovery ----------------------------------------------------------
    def list_properties(self):
        output = self._xfconf('-c', CHANNEL, '-l').stdout
        return [line.strip() for line in output.splitlines() if line.strip()]

    def image_properties(self):
        return [prop for prop in self.list_properties()
                if prop.startswith(BACKDROP_PREFIX) and prop.endswith(IMAGE_SUFFIX)]

    # --- applying -----------------------------------------------------------
    def apply(self, image_path):
        path = str(pathlib.Path(image_path))
        properties = self.image_properties()
        if not properties:
            raise PlatformError(
                'No Xfce backdrop properties were found in channel "%s". Is xfdesktop '
                'running in this session?' % CHANNEL)
        for prop in properties:
            self._xfconf('-c', CHANNEL, '-p', prop, '-s', path)
            if self.image_style is not None:
                style_prop = prop[:-len(IMAGE_SUFFIX)] + STYLE_SUFFIX
                self._xfconf('-c', CHANNEL, '-p', style_prop, '-n', '-t', 'int',
                             '-s', str(int(self.image_style)))
        return properties

    def current_all(self):
        values = []
        for prop in self.image_properties():
            result = self._xfconf('-c', CHANNEL, '-p', prop)
            text = (result.stdout or '').strip()
            if text:
                values.append((prop, text))
        return values

    def current(self):
        values = self.current_all()
        if not values:
            return None
        # Every screen and workspace is meant to show the same image.
        return values[0][1]

    def watch(self, image_path):
        raise PlatformError('Screen-change watching is not implemented for Xfce. '
                            'Xfce restores each monitor\'s own backdrop when a display is '
                            'reconnected; run "paperdrop run" if you want to re-apply.')

    # --- scheduling ---------------------------------------------------------
    def _unit_dir(self):
        base = self.environ.get('XDG_CONFIG_HOME')
        root = pathlib.Path(base) if base else self.home / '.config'
        return root / 'systemd' / 'user'

    def _unit_files(self):
        return (self._unit_dir() / SERVICE_NAME, self._unit_dir() / TIMER_NAME)

    def _unit_text(self, paths, daily_time):
        argv, env = runtime_command()
        # --config is a global option: it must come before the verb, or argparse
        # exits 2 and the timer silently does nothing.
        command = ' '.join(argv + ['--config', str(paths.config_file), 'run'])
        environment = ''.join('\nEnvironment=%s=%s' % (key, value)
                              for key, value in sorted(env.items()))
        hour, minute = daily_time.split(':')
        service = (
            '# Generated by PaperDrop. Safe to delete with "paperdrop uninstall".\n'
            '[Unit]\n'
            'Description=PaperDrop wallpaper update\n'
            'Documentation=https://github.com/PelikanM400/paperdrop\n\n'
            '[Service]\n'
            'Type=oneshot\n'
            'ExecStart=%s%s\n'
            % (command, environment))
        timer = (
            '# Generated by PaperDrop. Safe to delete with "paperdrop uninstall".\n'
            '[Unit]\n'
            'Description=PaperDrop daily wallpaper timer\n\n'
            '[Timer]\n'
            'OnCalendar=*-*-* %s:%s:00\n'
            'Persistent=true\n'
            'Unit=%s\n\n'
            '[Install]\n'
            'WantedBy=timers.target\n'
            % (hour, minute, SERVICE_NAME))
        return service, timer

    def _systemctl(self, *args):
        binary = self._systemctl_binary or shutil.which('systemctl')
        if not binary:
            return None
        return self._runner([binary, '--user', *args], env=dict(self.environ))

    def install(self, paths, daily_time):
        service_text, timer_text = self._unit_text(paths, daily_time)
        unit_dir = self._unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        created = []
        for path, text in ((unit_dir / SERVICE_NAME, service_text),
                           (unit_dir / TIMER_NAME, timer_text)):
            path.write_text(text, encoding='utf-8')
            created.append(str(path))
        if self._systemctl('daemon-reload') is None:
            raise PlatformError('systemctl was not found; the unit files were written to %s '
                                'but the timer was not enabled.' % unit_dir)
        result = self._systemctl('enable', '--now', TIMER_NAME)
        if result is None or result.returncode != 0:
            detail = (getattr(result, 'stderr', '') or '').strip()
            raise PlatformError('Could not enable %s: %s' % (TIMER_NAME, detail or 'unknown error'))
        return created

    def uninstall(self, purge=False):
        removed = []
        self._systemctl('disable', '--now', TIMER_NAME)
        for path in self._unit_files():
            if path.exists():
                path.unlink()
                removed.append(str(path))
        self._systemctl('daemon-reload')
        return removed

    def pause(self):
        result = self._systemctl('disable', '--now', TIMER_NAME)
        if result is None or result.returncode != 0:
            raise PlatformError('Could not pause the PaperDrop timer.')
        return 'paperdrop.timer disabled'

    def resume(self):
        result = self._systemctl('enable', '--now', TIMER_NAME)
        if result is None or result.returncode != 0:
            raise PlatformError('Could not resume the PaperDrop timer.')
        return 'paperdrop.timer enabled'

    def timer_state(self):
        result = self._systemctl('is-enabled', TIMER_NAME)
        if result is None:
            return 'unknown (systemctl not found)'
        return (result.stdout or result.stderr or 'unknown').strip()

    # --- reporting ----------------------------------------------------------
    def doctor(self):
        checks = []
        checks.append(('xfconf-query on PATH', xfconf_available(), shutil.which('xfconf-query') or 'missing'))
        display = self.environ.get('DISPLAY') or self.environ.get('WAYLAND_DISPLAY')
        checks.append(('graphical session environment', bool(display), display or 'DISPLAY is unset'))
        try:
            properties = self.image_properties()
            checks.append(('backdrop properties found', bool(properties),
                           '%d image propert%s' % (len(properties), 'y' if len(properties) == 1 else 'ies')))
        except PlatformError as error:
            checks.append(('backdrop properties found', False, str(error)))
        systemctl = shutil.which('systemctl')
        checks.append(('systemd --user available', bool(systemctl), systemctl or 'missing'))
        return checks

    def status_lines(self):
        lines = ['platform: xfce (channel %s)' % CHANNEL]
        try:
            values = self.current_all()
        except PlatformError as error:
            lines.append('applied wallpaper: unknown (%s)' % error)
            return lines
        if not values:
            lines.append('applied wallpaper: no backdrop properties visible in this session')
        for prop, value in values:
            lines.append('applied wallpaper: %s -> %s' % (prop, value))
        lines.append('scheduler: paperdrop.timer is %s' % self.timer_state())
        return lines
