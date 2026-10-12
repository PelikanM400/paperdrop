"""Configuration loading and strict validation.

Strict on purpose: a typo in a hand-edited file should be reported, not silently
ignored. Keys whose name starts with ``_`` are treated as comments.
"""
from __future__ import annotations

import json
import pathlib
import urllib.parse

from .errors import ConfigError
from .util import expand_path

ENV_CONFIG = 'PAPERDROP_CONFIG'

SCHEMA = {
    'source': {
        'mode': 'manifest',
        'url': '',
        'image_mode_acknowledged': False,
        'min_width': 0,
        'min_height': 0,
    },
    'cache_dir': '~/.cache/paperdrop',
    'state_file': '~/.local/state/paperdrop/state.json',
    'log': {'path': '~/.local/state/paperdrop/paperdrop.log', 'max_bytes': 1048576, 'keep': 2},
    'http': {'timeout_seconds': 60, 'retries': 3, 'max_redirects': 5,
             'max_bytes': 67108864, 'allow_http': False},
    'verify': {'max_pixels': 100000000},
    'apply': {'screens': 'all', 'scaling': 'fill', 'helper_path': '', 'xfce_image_style': None,
              'confirm_attempts': 5, 'confirm_delay_seconds': 0.8},
    'schedule': {'daily_time': '07:35', 'catch_up_on_login': True, 'watch_screen_changes': True,
                 'catch_up_interval_minutes': 60, 'screen_poll_seconds': 30},
}

MODES = ('manifest', 'image')
SCALINGS = ('fill',)


def _merge(defaults, given, path=''):
    """Overlay `given` on `defaults`, rejecting unknown keys."""
    if not isinstance(given, dict):
        raise ConfigError('Expected an object at "%s".' % (path or '<root>'))
    result = {}
    for key, value in given.items():
        if key.startswith('_'):
            continue
        if key not in defaults:
            allowed = ', '.join(sorted(k for k in defaults if not k.startswith('_')))
            raise ConfigError('Unknown configuration key "%s%s". Allowed here: %s.'
                              % (path, key, allowed))
        default = defaults[key]
        if isinstance(default, dict):
            result[key] = _merge(default, value, path + key + '.')
        else:
            result[key] = value
    for key, value in defaults.items():
        if isinstance(value, dict):
            result.setdefault(key, _merge(value, {}, path + key + '.'))
        else:
            result.setdefault(key, value)
    return result


def _require_positive_int(config, key, minimum=1):
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError('"%s" must be an integer >= %d.' % (key, minimum))
    return value


def validate(config):
    """Return a validated copy of a full config mapping."""
    if not isinstance(config, dict):
        raise ConfigError('Configuration must be a JSON object.')
    merged = _merge(SCHEMA, config)
    source = merged['source']
    if source['mode'] not in MODES:
        raise ConfigError('source.mode must be one of: %s.' % ', '.join(MODES))
    url = source['url']
    if not isinstance(url, str) or not url.strip():
        raise ConfigError('source.url is required.')
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ('http', 'https'):
        raise ConfigError('source.url must be an http(s) URL.')
    if parts.username or parts.password:
        raise ConfigError('source.url must not contain credentials.')
    if parts.scheme == 'http' and not merged['http']['allow_http']:
        raise ConfigError('Plain http is refused; set http.allow_http to true to override.')
    if source['mode'] == 'image' and source['image_mode_acknowledged'] is not True:
        raise ConfigError(
            'Image mode has no manifest consistency guarantee. Set '
            'source.image_mode_acknowledged to true to accept that, or use manifest mode.')
    for key in ('min_width', 'min_height'):
        value = source[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError('source.%s must be an integer >= 0.' % key)
    if merged['apply']['scaling'] not in SCALINGS:
        raise ConfigError('apply.scaling must be one of: %s.' % ', '.join(SCALINGS))
    if merged['apply']['screens'] not in ('all',):
        raise ConfigError('apply.screens currently supports only "all".')
    style = merged['apply']['xfce_image_style']
    if style is not None and (isinstance(style, bool) or not isinstance(style, int)):
        raise ConfigError('apply.xfce_image_style must be null or an integer.')
    _require_positive_int(merged['apply'], 'confirm_attempts', minimum=1)
    confirm_delay = merged['apply']['confirm_delay_seconds']
    if isinstance(confirm_delay, bool) or not isinstance(confirm_delay, (int, float)) or confirm_delay < 0:
        raise ConfigError('apply.confirm_delay_seconds must be a number >= 0.')
    catch_up = merged['schedule']['catch_up_interval_minutes']
    if isinstance(catch_up, bool) or not isinstance(catch_up, int) or catch_up < 0:
        raise ConfigError('schedule.catch_up_interval_minutes must be an integer >= 0 '
                          '(0 disables the periodic catch-up).')
    poll = merged['schedule']['screen_poll_seconds']
    if isinstance(poll, bool) or not isinstance(poll, int) or poll < 0:
        raise ConfigError('schedule.screen_poll_seconds must be an integer >= 0 '
                          '(0 disables the resident watcher\'s screen poll).')
    for key in ('timeout_seconds', 'retries', 'max_redirects', 'max_bytes'):
        _require_positive_int(merged['http'], key, minimum=1)
    _require_positive_int(merged['verify'], 'max_pixels', minimum=1)
    _require_positive_int(merged['log'], 'max_bytes', minimum=1024)
    _require_positive_int(merged['log'], 'keep', minimum=1)
    for key in ('cache_dir', 'state_file'):
        if not isinstance(merged[key], str) or not merged[key].strip():
            raise ConfigError('"%s" must be a non-empty path.' % key)
    if not isinstance(merged['log']['path'], str) or not merged['log']['path'].strip():
        raise ConfigError('log.path must be a non-empty path.')
    daily_time = merged['schedule']['daily_time']
    if not (isinstance(daily_time, str) and len(daily_time) == 5 and daily_time[2] == ':'
            and daily_time[:2].isdigit() and daily_time[3:].isdigit()
            and int(daily_time[:2]) < 24 and int(daily_time[3:]) < 60):
        raise ConfigError('schedule.daily_time must look like "07:35" (24-hour).')
    for key in ('catch_up_on_login', 'watch_screen_changes'):
        if not isinstance(merged['schedule'][key], bool):
            raise ConfigError('schedule.%s must be true or false.' % key)
    return merged


def load_config(path=None):
    """Load and validate a config file. Missing file is an error with guidance."""
    from .paths import default_config_file
    path = pathlib.Path(path) if path else default_config_file()
    if not path.is_file():
        raise ConfigError(
            'No configuration file at %s.\n'
            'Create one with:\n'
            '  mkdir -p %s\n'
            '  paperdrop --print-example-config > %s\n'
            'then set source.url in it (and source.image_mode_acknowledged if you '
            'deliberately choose image mode).' % (path, path.parent, path))
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ConfigError('Could not read %s: %s' % (path, error)) from None
    return validate(raw), path


def example_config():
    """A commented example, used by `paperdrop --print-example-config`."""
    return {
        '_comment': 'PaperDrop configuration. Paths are user-scoped; ~ expands.',
        '_comment_source': ('mode=manifest reads a JSON manifest (recommended: it carries a '
                            'sha256, so an unchanged image is never re-downloaded). '
                            'mode=image treats the URL as a plain always-updated image and '
                            'has NO manifest consistency guarantee.'),
        'source': {
            'mode': 'manifest',
            'url': 'https://example.com/wallpapers/desk.json',
            'image_mode_acknowledged': False,
            'min_width': 1920,
            'min_height': 1080,
        },
        '_comment_paths': 'Where the current and previous image are cached.',
        'cache_dir': '~/.cache/paperdrop',
        'state_file': '~/.local/state/paperdrop/state.json',
        '_comment_log': ('One rotating file: max_bytes per file, keep backups, so the '
                         'rotating log is bounded by max_bytes * (keep + 1). The files '
                         'macOS writes for the scheduled runs are capped at max_bytes each '
                         'too, which keeps every log PaperDrop can write at about 5 MB in '
                         'total (1 MB x 3 + 1 MB x 2).'),
        'log': {'path': '~/.local/state/paperdrop/paperdrop.log',
                'max_bytes': 1048576, 'keep': 2},
        '_comment_http': ('max_bytes caps a single download; retries apply only to transient '
                          'failures (timeouts, 429, 5xx).'),
        'http': {'timeout_seconds': 60, 'retries': 3, 'max_redirects': 5,
                 'max_bytes': 67108864, 'allow_http': False},
        'verify': {'max_pixels': 100000000},
        '_comment_apply': ('scaling=fill keeps the aspect ratio and fills the screen. '
                           'helper_path is the compiled macOS helper (leave empty to '
                           'auto-detect). xfce_image_style stays null to preserve whatever '
                           'background style you configured in Xfce. '
                           'confirm_attempts/confirm_delay_seconds control how long to wait '
                           'for the desktop to report the new wallpaper: some desktops keep '
                           'answering with the old one for a moment after the change.'),
        'apply': {'screens': 'all', 'scaling': 'fill', 'helper_path': '',
                  'xfce_image_style': None, 'confirm_attempts': 5,
                  'confirm_delay_seconds': 0.8},
        '_comment_schedule': ('Used by the installer to create the user-level schedule. '
                              'catch_up_interval_minutes adds a periodic cheap check so a run '
                              'missed while the machine was asleep happens soon after it '
                              'wakes; the same-day check makes those runs nearly free. '
                              '0 disables it. catch_up_on_login adds a run at login (macOS '
                              'RunAtLoad; systemd Persistent on Linux), and '
                              'watch_screen_changes installs the resident macOS watcher that '
                              're-applies the image after a screen change or a wake. '
                              'screen_poll_seconds is how often that watcher asks the desktop '
                              'for every screen, because a display reconnected after a run can '
                              'come back with the wallpaper it remembered without any event '
                              'reaching us (0 turns the poll off). All of them are read at run '
                              'time: change them and run "paperdrop install" again for the '
                              'ones the installer uses.'),
        'schedule': {'daily_time': '07:35', 'catch_up_on_login': True,
                     'watch_screen_changes': True, 'catch_up_interval_minutes': 60,
                     'screen_poll_seconds': 30},
    }


def normalized_paths(config):
    """Convenience: expand the path-ish values for display."""
    return {
        'cache_dir': expand_path(config['cache_dir']),
        'state_file': expand_path(config['state_file']),
        'log_file': expand_path(config['log']['path']),
    }
