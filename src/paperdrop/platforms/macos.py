"""macOS adapter: a small AppKit helper plus user-level LaunchAgents.

The Swift helper (``paperdrop-set``) does the two things that need AppKit:
applying one image file to every screen with aspect-preserving fill, and
re-applying it when the display configuration changes.

PARTLY VERIFIED on one machine (MacBook Air M5, macOS 27.0.1, two displays):
applying, dual-screen fill without stretching, all desktop spaces updating and
wake-from-sleep behaved. Reconnecting an external display appeared to work on
that machine, but a second Mac showed that the screen-parameter notification
never reaches the headless helper at all (only wake events arrived) and its
external display kept the old image: the reconnection case is not treated as
verified, and `screen_poll_loop` is what covers it. Still unverified there: the
scheduled runs themselves (LaunchAgent timing, login catch-up, periodic catch-up
after a wake) and what a quarantined helper looks like.
"""
from __future__ import annotations

import os
import pathlib
import plistlib
import shutil
import subprocess

from ..errors import PlatformError
from ..journal import shrink_file
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

    def desktop_state(self):
        """One entry per screen: ('screen <index> <name>', wallpaper path or None).

        The helper's `screens` output is tab separated (index, name, geometry,
        scale, path). A line that does not parse is skipped: guessing about a
        screen would be worse than leaving it out.
        """
        state = []
        for line in self.screens():
            fields = [field.strip() for field in line.split('\t')]
            if len(fields) < 5:
                continue
            index, name, _geometry, _scale, path = fields[:5]
            state.append(('screen %s %s' % (index, name), path or None))
        return state

    def watch(self, cache_dir, lock_file):
        """Each native resolve/apply event uses the configured updater lock.

        The helper is handed the cache *directory*, not an image: it reads the role
        index itself on every event (a startup image would freeze a stale choice).
        """
        return subprocess.call([self.helper(), 'watch-cache', str(cache_dir),
                                '--lock-file', str(lock_file)], env=dict(self.environ))

    # --- scheduling ---------------------------------------------------------
    def _agent_dir(self):
        return self.home / 'Library' / 'LaunchAgents'

    def cap_own_logs(self, limit):
        """launchd owns our two log files and never rotates them."""
        shrunk = []
        for label in (APPLY_LABEL, WATCH_LABEL):
            for kind in ('out', 'err'):
                path = self._log_dir() / ('%s.%s.log' % (label, kind))
                before = shrink_file(path, limit)
                if before:
                    shrunk.append((str(path), before))
        return shrunk

    def _log_dir(self):
        return self.home / 'Library' / 'Logs'

    def _agent_path(self, label):
        return self._agent_dir() / (label + '.plist')

    def _parked_dir(self):
        """Where a paused agent's plist waits, outside the auto-loaded directory."""
        return self.home / 'Library' / 'Application Support' / 'PaperDrop' / 'paused'

    def _parked_path(self, label):
        return self._parked_dir() / (label + '.plist')

    def _watch_enabled(self):
        """schedule.watch_screen_changes: install the resident watcher or not."""
        return bool((self.config.get('schedule') or {}).get('watch_screen_changes', True))

    def _catch_up_on_login(self):
        """schedule.catch_up_on_login: run once at login (covers an off machine)."""
        return bool((self.config.get('schedule') or {}).get('catch_up_on_login', True))

    def _wanted_labels(self):
        return (APPLY_LABEL,) + ((WATCH_LABEL,) if self._watch_enabled() else ())

    def agent_state(self, label):
        """`installed` / `paused` / `disabled` / `not installed`, for status/doctor."""
        if label == WATCH_LABEL and not self._watch_enabled():
            return 'disabled'
        if self._agent_path(label).exists():
            return 'installed'
        if self._parked_path(label).exists():
            return 'paused'
        return 'not installed'

    def _payload(self, label, arguments, paths, extra=None):
        argv, env = runtime_command()
        payload = {
            'Label': label,
            # --config is a global option, so it has to precede the verb:
            # argparse rejects "paperdrop run --config X" as a usage error
            # (exit 2), which is exactly what launchd recorded before this fix.
            'ProgramArguments': argv + ['--config', str(paths.config_file)] + arguments,
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
        catch_up_minutes = int((self.config.get('schedule') or {})
                               .get('catch_up_interval_minutes', 60) or 0)
        agents = self._agent_dir()
        logs = self._log_dir()
        agents.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        created = []
        triggers = {'StartCalendarInterval': {'Hour': hour, 'Minute': minute}}
        if self._catch_up_on_login():
            # A run at login also covers a machine that was off at the daily time;
            # with it switched off, the periodic catch-up picks the work up instead.
            triggers['RunAtLoad'] = True
        if catch_up_minutes > 0:
            # launchd does not fire a calendar trigger while the machine sleeps, so a
            # daily run missed overnight would otherwise wait a whole day. A periodic
            # trigger catches it soon after wake; the same-day check makes those extra
            # runs cheap (no download, no re-apply).
            triggers['StartInterval'] = catch_up_minutes * 60
        apply_payload = self._payload(APPLY_LABEL, ['run'], paths, extra=dict(triggers))
        payloads = [(APPLY_LABEL, apply_payload)]
        if self._watch_enabled():
            payloads.append((WATCH_LABEL, self._payload(
                WATCH_LABEL, ['watch'], paths, extra={'RunAtLoad': True, 'KeepAlive': True})))
        else:
            # schedule.watch_screen_changes = false: leave no stale watcher behind.
            self._launchctl('bootout', 'gui/%s/%s' % (str(os.getuid()), WATCH_LABEL))
            self._agent_path(WATCH_LABEL).unlink(missing_ok=True)
        for label, payload in payloads:
            target = self._agent_path(label)
            with open(target, 'wb') as stream:
                plistlib.dump(payload, stream)
            # A fresh install is an active install: it also un-parks the label.
            self._parked_path(label).unlink(missing_ok=True)
            created.append(str(target))
        if self._launchctl('list') is None:
            raise PlatformError('launchctl was not found; the agent files were written to %s but '
                                'were not loaded.' % agents)
        uid = str(os.getuid())
        for label, _payload in payloads:
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
            for target in (self._agent_path(label), self._parked_path(label)):
                if target.exists():
                    target.unlink()
                    removed.append(str(target))
        return removed

    def pause(self):
        """Unload the agents and park their plists outside the auto-loaded directory.

        `launchctl bootout` on its own only lasts until the next login: launchd
        loads every plist in ~/Library/LaunchAgents when the user logs in, so a
        paused schedule would silently come back after a restart. Moving the files
        aside is what makes the pause real, and `resume` moves them back.
        """
        uid = str(os.getuid())
        parked = []
        for label in (APPLY_LABEL, WATCH_LABEL):
            self._launchctl('bootout', 'gui/%s/%s' % (uid, label))
            target = self._agent_path(label)
            if target.exists():
                self._parked_dir().mkdir(parents=True, exist_ok=True)
                os.replace(target, self._parked_path(label))
                parked.append(label)
        if not parked:
            return 'LaunchAgents were not installed; nothing to pause'
        return ('LaunchAgents unloaded and parked in %s (this survives a restart; '
                '"paperdrop resume" reloads them)' % self._parked_dir())

    def resume(self):
        uid = str(os.getuid())
        loaded = []
        for label in self._wanted_labels():
            parked = self._parked_path(label)
            target = self._agent_path(label)
            if parked.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(parked, target)
                loaded.append(label)
            if target.exists():
                self._launchctl('bootstrap', 'gui/%s' % uid, str(target))
        if not loaded and not any(self._agent_path(label).exists()
                                  for label in self._wanted_labels()):
            return 'nothing to resume: run "paperdrop install" first'
        return 'LaunchAgents loaded (%s)' % ', '.join(self._wanted_labels())

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
            state = self.agent_state(label)
            detail = {
                'installed': str(self._agent_path(label)),
                'paused': 'paused: run "paperdrop resume" to reload it',
                'disabled': 'disabled: schedule.watch_screen_changes = false',
                'not installed': 'not installed',
            }[state]
            checks.append(('agent %s installed' % label, state != 'not installed', detail))
        return checks

    def status_lines(self):
        lines = ['platform: macOS']
        catch_up = int((self.config.get('schedule') or {})
                       .get('catch_up_interval_minutes', 60) or 0)
        lines.append('scheduled: daily%s' % (
            ' plus a catch-up check every %d minutes' % catch_up if catch_up > 0
            else ' (periodic catch-up disabled)'))
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
            state = self.agent_state(label)
            if state == 'paused':
                lines.append('agent %s: paused (run "paperdrop resume")' % label)
            elif state == 'disabled':
                lines.append('agent %s: disabled (schedule.watch_screen_changes = false)' % label)
            else:
                lines.append('agent %s: %s' % (label, state))
        return lines
