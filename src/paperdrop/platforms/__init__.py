"""Desktop platform adapters.

Only two things are genuinely platform specific: how to set the wallpaper, and
how to schedule the daily run. Everything else lives in the shared core.
"""
from __future__ import annotations

import sys

from .. import __version__
from ..errors import PlatformError

__all__ = ['detect_platform', 'PlatformError']


def detect_platform(config=None, environ=None, which=None):
    """Return the adapter for this machine, or raise PlatformError."""
    if sys.platform == 'darwin':
        from .macos import MacOS
        return MacOS(config=config)
    from .xfce import Xfce, xfconf_available
    if xfconf_available(which=which):
        return Xfce(config=config, environ=environ)
    raise PlatformError(
        'No supported desktop found. PaperDrop %s supports macOS and Xfce '
        '(xfconf-query was not found). Other desktops are not supported yet.'
        % __version__)
