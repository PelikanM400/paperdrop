"""Bounded logging: one rotating file plus optional console output."""
from __future__ import annotations

import logging
import pathlib
import sys
from logging.handlers import RotatingFileHandler

from .util import ensure_dir

LOGGER_NAME = 'paperdrop'

TRUNCATION_NOTE = b'--- paperdrop: earlier lines were dropped to keep this log within its cap ---\n'


def shrink_file(path, limit, keep=None):
    """Trim a log file that another process holds open for appending.

    Returns the size it had before shrinking, or 0 when nothing was needed.

    Overwriting in place — never replacing the file — is what makes this safe:
    launchd keeps its own file descriptor, so a replaced file would go on
    receiving the writes while the new file sat empty. Truncating is fine for an
    O_APPEND writer, because the kernel seeks to the end before each write.
    """
    path = pathlib.Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if size <= limit:
        return 0
    keep = max(1, limit // 2) if keep is None else keep
    try:
        with open(path, 'rb') as handle:
            handle.seek(max(0, size - keep))
            tail = handle.read()
        cut = tail.find(b'\n')
        if 0 <= cut < len(tail) - 1:
            tail = tail[cut + 1:]          # start on a line boundary
        with open(path, 'r+b') as handle:
            handle.seek(0)
            handle.write(TRUNCATION_NOTE + tail)
            handle.truncate()
    except OSError as error:  # pragma: no cover - unwritable log location
        print('paperdrop: could not shrink %s: %s' % (path, error), file=sys.stderr)
        return 0
    return size


def build_logger(log_path=None, max_bytes=262144, keep=2, verbose=False, console=True):
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    for stale in list(logger.handlers):
        logger.removeHandler(stale)
        stale.close()          # otherwise the log file stays open until GC
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
