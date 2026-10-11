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

    def watch(self, image_path) -> int:
        """Block, re-applying `image_path` whenever the screen layout changes."""
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


def require(executable):
    path = shutil.which(executable)
    if not path:
        raise PlatformError('Required command not found: %s' % executable)
    return path
