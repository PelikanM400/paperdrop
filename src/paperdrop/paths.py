"""Default locations. Everything is user-scoped; nothing writes to system paths."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

from .util import expand_path

APP_DIR = 'paperdrop'


def home_dir():
    """Base home for PaperDrop defaults; PAPERDROP_HOME makes that testable."""
    override = os.environ.get('PAPERDROP_HOME')
    if override:
        return pathlib.Path(os.path.expanduser(override))
    return pathlib.Path(os.path.expanduser('~'))


def default_config_file():
    from .config import ENV_CONFIG
    override = os.environ.get(ENV_CONFIG)
    if override:
        return pathlib.Path(os.path.expanduser(override))
    return home_dir() / '.config' / APP_DIR / 'config.json'


@dataclass(frozen=True)
class Paths:
    config_file: pathlib.Path
    cache_dir: pathlib.Path
    state_file: pathlib.Path
    log_file: pathlib.Path
    lock_file: pathlib.Path

    @classmethod
    def from_config(cls, config, config_file=None):
        if config_file is not None:
            config_file = pathlib.Path(config_file)
        else:
            config_file = default_config_file()
        cache_dir = expand_path(config['cache_dir'])
        state_file = expand_path(config['state_file'])
        log_file = expand_path(config['log']['path'])
        lock_file = state_file.parent / (APP_DIR + '.lock')
        return cls(config_file=config_file, cache_dir=cache_dir, state_file=state_file,
                   log_file=log_file, lock_file=lock_file)
