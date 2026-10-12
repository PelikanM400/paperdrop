"""Shared adapter interface and helpers used by the installers."""
from __future__ import annotations

import pathlib
import shutil
import sys

from ..errors import PlatformError


def runtime_command():
    """(argv_prefix, env) that runs `paperdrop <verb>` with this interpreter.

    Works both from an installed package and from a source checkout, so the
    generated scheduler entry does not depend on how PaperDrop was installed.
    """
    package = pathlib.Path(__file__).resolve().parents[1]          # .../paperdrop
    parent = package.parent                                        # .../src or site-packages
    argv = [sys.executable, '-m', 'paperdrop']
    env = {}
    if (parent / 'paperdrop' / '__init__.py').exists():
        env['PYTHONPATH'] = str(parent)
    return argv, env


class Platform:
    name = 'unknown'
    supports_screen_watch = False

    def __init__(self, config=None):
        self.config = config or {}

    # --- required -----------------------------------------------------------
    def apply(self, image_path) -> list:
        """Set `image_path` as the wallpaper on every screen."""
        raise NotImplementedError

    def current(self):
        """Return the currently applied wallpaper path, or None if unknown."""
        raise NotImplementedError

    def desktop_state(self) -> list:
        """Return [(target, wallpaper path), ...] for every screen or desktop entry.

        `current()` answers for one screen, which is not enough to notice a second
        display that kept an older image (AppKit reports the main screen only). A
        platform that can see all of its screens returns them here; returning an
        empty list means "cannot tell", and callers fall back to `current()`.
        """
        return []

    def watch(self, cache_dir, lock_file=None) -> int:
        """Block, re-resolving and re-applying the active cache image on every event."""
        raise PlatformError('%s: watching for screen changes is not supported.' % self.name)

    # --- scheduling ---------------------------------------------------------
    def install(self, paths, daily_time) -> list:
        raise PlatformError('%s: install is not implemented.' % self.name)

    def uninstall(self, purge=False) -> list:
        raise PlatformError('%s: uninstall is not implemented.' % self.name)

    def pause(self) -> str:
        raise PlatformError('%s: pause is not implemented.' % self.name)

    def resume(self) -> str:
        raise PlatformError('%s: resume is not implemented.' % self.name)

    # --- reporting ----------------------------------------------------------
    def doctor(self) -> list:
        """Return a list of (check, ok, detail) tuples."""
        return []

    def status_lines(self) -> list:
        return []

    # --- log budget ---------------------------------------------------------
    def cap_own_logs(self, limit) -> list:
        """Shrink the log files the operating system keeps for our runs.

        Returns [(path, size_before)] for each file that was shrunk. PaperDrop's
        own rotating log bounds itself; these are the files we do not own and
        would otherwise grow without limit.
        """
        return []


def require(executable):
    path = shutil.which(executable)
    if not path:
        raise PlatformError('Required command not found: %s' % executable)
    return path
