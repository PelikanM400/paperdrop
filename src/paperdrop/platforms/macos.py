"""macOS adapter: a small AppKit helper plus user-level LaunchAgents.

The Swift helper (``paperdrop-set``) does the two things that need AppKit:
applying one image file to every screen with aspect-preserving fill, and
re-applying it when the display configuration changes.

UNVERIFIED: this module has not been exercised on a real Mac yet. Dual-screen
behaviour, Spaces, external-display reconnection and wake-from-sleep must be
validated on the target machine before the README claims support.
"""
from __future__ import annotations

import os
import pathlib
import plistlib
import shutil
import subprocess

from ..errors import PlatformError
from .base import Platform, runtime_command

HELPER_NAME = 'paperdrop-set'
APPLY_LABEL = 'dev.paperdrop.apply'
WATCH_LABEL = 'dev.paperdrop.watch'
LAUNCHCTL = 'launchctl'


def _default_runner(argv, env=None):
    return subprocess.run(argv, capture_output=True, text=True, env=env)


class MacOS(Platform):
    name = 'macos'
    supports_screen_watch = True

    def __init__(self, config=None, environ=None, runner=None, home=None):
        super().__init__(config)
        self.environ = dict(os.environ if environ is None else environ)
        self._runner = runner or _default_runner
        self.home = pathlib.Path(home or self.environ.get('HOME') or pathlib.Path.home())
        self.helper_path = ((config or {}).get('apply', {}) or {}).get('helper_path') or ''

    # --- helper -------------------------------------------------------------
    def helper(self):
        if self.helper_path:
            candidate = pathlib.Path(self.helper_path).expanduser()
            if candidate.is_file():
                return str(candidate)
            raise PlatformError('apply.helper_path does not exist: %s' % candidate)
        found = shutil.which(HELPER_NAME)
        if found:
            return found
        fallback = self.home / 'Library' / 'Application Support' / 'PaperDrop' / HELPER_NAME
        if fallback.is_file():
            return str(fallback)
        raise PlatformError(
            'The macOS helper "%s" was not found. Build it with macos/build.sh and then either '
            'put it on PATH, install it to ~/Library/Application Support/PaperDrop/, or set '
            'apply.helper_path.' % HELPER_NAME)

    def _helper(self, *args):
        result = self._runner([self.helper(), *args], env=dict(self.environ))
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            raise PlatformError('%s %s failed: %s' % (HELPER_NAME, ' '.join(args), detail))
        return result

    # --- applying -----------------------------------------------------------
    def apply(self, image_path):
        path = str(pathlib.Path(image_path))
        if not pathlib.Path(path).is_file():
            raise PlatformError('Cached image is missing: %s' % path)
        self._helper('apply', path, '--all-screens', '--fill')
        return [path]

    def current(self):
        result = self._helper('current')
        value = (result.stdout or '').strip()
        return value or None

    def screens(self):
        result = self._helper('screens')
        return [line for line in (result.stdout or '').splitlines() if line.strip()]

    def watch(self, image_path):
        """Run the resident helper that reacts to display configuration changes."""
        path = str(pathlib.Path(image_path))
        return subprocess.call([self.helper(), 'watch', path], env=dict(self.environ))

    # --- scheduling ---------------------------------------------------------
    def _agent_dir(self):
        return self.home / 'Library' / 'LaunchAgents'

    def _log_dir(self):
        return self.home / 'Library' / 'Logs'

    def _agent_path(self, label):
        return self._agent_dir() / (label + '.plist')

    def _payload(self, label, arguments, paths, extra=None):
        argv, env = runtime_command()
        payload = {
            'Label': label,
            'ProgramArguments': argv + arguments + ['--config', str(paths.config_file)],
            'LimitLoadToSessionType': 'Aqua',
            'ProcessType': 'Background',
            'StandardOutPath': str(self._log_dir() / (label + '.out.log')),
            'StandardErrorPath': str(self._log_dir() / (label + '.err.log')),
            'WorkingDirectory': str(self.home),
        }
        if env:
            payload['EnvironmentVariables'] = env
        if extra:
            payload.update(extra)
        return payload

    def _launchctl(self, *args):
        binary = shutil.which(LAUNCHCTL)
        if not binary:
            return None
        return self._runner([binary, *args], env=dict(self.environ))

    def install(self, paths, daily_time):
        hour, minute = (int(part) for part in daily_time.split(':'))
        agents = self._agent_dir()
        logs = self._log_dir()
        agents.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        created = []
        apply_payload = self._payload(
            APPLY_LABEL, ['run'],
            paths,
            extra={
                'RunAtLoad': True,
                'StartCalendarInterval': {'Hour': hour, 'Minute': minute},
            })
        watch_payload = self._payload(
            WATCH_LABEL, ['watch'],
            paths,
            extra={'RunAtLoad': True, 'KeepAlive': True})
        for label, payload in ((APPLY_LABEL, apply_payload), (WATCH_LABEL, watch_payload)):
            target = self._agent_path(label)
            with open(target, 'wb') as stream:
                plistlib.dump(payload, stream)
            created.append(str(target))
        if self._launchctl('list') is None:
            raise PlatformError('launchctl was not found; the agent files were written to %s but '
                                'were not loaded.' % agents)
        uid = str(os.getuid())
        for label in (APPLY_LABEL, WATCH_LABEL):
            self._launchctl('bootout', 'gui/%s/%s' % (uid, label))
            result = self._launchctl('bootstrap', 'gui/%s' % uid, str(self._agent_path(label)))
            if result is None or result.returncode != 0:
                detail = (getattr(result, 'stderr', '') or '').strip()
                raise PlatformError('Could not load %s: %s' % (label, detail or 'unknown error'))
        return created

    def uninstall(self, purge=False):
        uid = str(os.getuid())
        removed = []
        for label in (APPLY_LABEL, WATCH_LABEL):
            self._launchctl('bootout', 'gui/%s/%s' % (uid, label))
            target = self._agent_path(label)
            if target.exists():
                target.unlink()
                removed.append(str(target))
        return removed

    def pause(self):
        uid = str(os.getuid())
        for label in (APPLY_LABEL, WATCH_LABEL):
            self._launchctl('bootout', 'gui/%s/%s' % (uid, label))
        return 'LaunchAgents unloaded (resume reloads them)'

    def resume(self):
        uid = str(os.getuid())
        for label in (APPLY_LABEL, WATCH_LABEL):
            target = self._agent_path(label)
            if target.exists():
                self._launchctl('bootstrap', 'gui/%s' % uid, str(target))
        return 'LaunchAgents loaded'

    # --- reporting ----------------------------------------------------------
    def doctor(self):
        checks = []
        try:
            path = self.helper()
            checks.append(('helper binary', True, path))
        except PlatformError as error:
            checks.append(('helper binary', False, str(error)))
        checks.append(('launchctl available', bool(shutil.which(LAUNCHCTL)),
                       shutil.which(LAUNCHCTL) or 'missing'))
        for label in (APPLY_LABEL, WATCH_LABEL):
            exists = self._agent_path(label).exists()
            checks.append(('agent %s installed' % label, exists,
                           str(self._agent_path(label)) if exists else 'not installed'))
        return checks

    def status_lines(self):
        lines = ['platform: macOS']
        try:
            applied = self.current()
        except PlatformError as error:
            applied = None
            lines.append('applied wallpaper: unknown (%s)' % error)
        if applied:
            lines.append('applied wallpaper: %s' % applied)
        try:
            for screen in self.screens():
                lines.append('screen: %s' % screen)
        except PlatformError:
            pass
        for label in (APPLY_LABEL, WATCH_LABEL):
            state = 'installed' if self._agent_path(label).exists() else 'not installed'
            lines.append('agent %s: %s' % (label, state))
        return lines
