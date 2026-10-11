"""Bounded logging: one rotating file plus optional console output."""
from __future__ import annotations

import logging
import pathlib
import sys
from logging.handlers import RotatingFileHandler

from .util import ensure_dir

LOGGER_NAME = 'paperdrop'


def build_logger(log_path=None, max_bytes=262144, keep=2, verbose=False, console=True):
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

    if log_path is not None:
        try:
            ensure_dir(pathlib.Path(log_path).parent)
            handler = RotatingFileHandler(str(log_path), maxBytes=max_bytes,
                                          backupCount=max(1, keep), encoding='utf-8')
            handler.setFormatter(formatter)
            handler.setLevel(logging.DEBUG if verbose else logging.INFO)
            logger.addHandler(handler)
        except OSError as error:  # pragma: no cover - unwritable log location
            print('paperdrop: could not open log file: %s' % error, file=sys.stderr)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
        stream.setLevel(logging.DEBUG if verbose else logging.WARNING)
        logger.addHandler(stream)
    return logger
