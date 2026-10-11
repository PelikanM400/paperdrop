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
    'log': {'path': '~/.local/state/paperdrop/paperdrop.log', 'max_bytes': 262144, 'keep': 2},
    'http': {'timeout_seconds': 60, 'retries': 3, 'max_redirects': 5,
             'max_bytes': 67108864, 'allow_http': False},
    'verify': {'max_pixels': 100000000},
    'apply': {'screens': 'all', 'scaling': 'fill', 'helper_path': '', 'xfce_image_style': None},
    'schedule': {'daily_time': '05:05', 'catch_up_on_login': True, 'watch_screen_changes': True},
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
        raise ConfigError('schedule.daily_time must look like "05:05" (24-hour).')
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
        'log': {'path': '~/.local/state/paperdrop/paperdrop.log',
                'max_bytes': 262144, 'keep': 2},
        '_comment_http': ('max_bytes caps a single download; retries apply only to transient '
                          'failures (timeouts, 429, 5xx).'),
        'http': {'timeout_seconds': 60, 'retries': 3, 'max_redirects': 5,
                 'max_bytes': 67108864, 'allow_http': False},
        'verify': {'max_pixels': 100000000},
        '_comment_apply': ('scaling=fill keeps the aspect ratio and fills the screen. '
                           'helper_path is the compiled macOS helper (leave empty to '
                           'auto-detect). xfce_image_style stays null to preserve whatever '
                           'background style you configured in Xfce.'),
        'apply': {'screens': 'all', 'scaling': 'fill', 'helper_path': '',
                  'xfce_image_style': None},
        '_comment_schedule': 'Used by the installer to create the user-level schedule.',
        'schedule': {'daily_time': '05:05', 'catch_up_on_login': True,
                     'watch_screen_changes': True},
    }


def normalized_paths(config):
    """Convenience: expand the path-ish values for display."""
    return {
        'cache_dir': expand_path(config['cache_dir']),
        'state_file': expand_path(config['state_file']),
        'log_file': expand_path(config['log']['path']),
    }
