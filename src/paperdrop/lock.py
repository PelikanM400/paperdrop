"""Single-instance lock, so two schedules cannot fight over the wallpaper."""
from __future__ import annotations

import fcntl
import pathlib

from .errors import BusyError
from .util import ensure_dir


class FileLock:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self._handle = None

    def acquire(self):
        ensure_dir(self.path.parent)
        handle = open(self.path, 'a+')
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise BusyError('Another PaperDrop run is already in progress.') from None
        self._handle = handle
        return self

    def release(self):
        if self._handle is not None:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
