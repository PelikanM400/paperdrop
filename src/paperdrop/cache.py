"""Cache of the current and previous image.

Only two images are ever kept. Publishing is atomic: a partially written file is
never visible under the published name, so a crash or a failed check can never
leave a broken wallpaper behind.
"""
from __future__ import annotations

import os
import pathlib
import secrets
from typing import Optional

from .util import ensure_dir


class Cache:
    CURRENT = 'current'
    PREVIOUS = 'previous'

    def __init__(self, directory):
        self.directory = pathlib.Path(directory)

    def ensure(self):
        ensure_dir(self.directory)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:  # pragma: no cover - non-POSIX or unusual mounts
            pass
        return self.directory

    def _find(self, stem):
        if not self.directory.is_dir():
            return None
        candidates = sorted(p for p in self.directory.glob(stem + '.*')
                            if p.is_file() and not p.name.startswith('.'))
        return candidates[0] if candidates else None

    def current(self):
        return self._find(self.CURRENT)

    def previous(self):
        return self._find(self.PREVIOUS)

    def staged_path(self, suffix):
        """A private temp name in the cache directory (same filesystem)."""
        self.ensure()
        return self.directory / ('.incoming-' + secrets.token_hex(6) + suffix)

    def publish(self, data, extension):
        """Atomically install `data` as the current image, demoting the old one."""
        extension = extension if extension.startswith('.') else '.' + extension
        self.ensure()
        staged = self.staged_path(extension)
        fd = os.open(str(staged), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            old_previous = self.previous()
            if old_previous is not None:
                old_previous.unlink(missing_ok=True)
            old_current = self.current()
            if old_current is not None:
                target = self.directory / (self.PREVIOUS + old_current.suffix)
                os.replace(str(old_current), str(target))
            final = self.directory / (self.CURRENT + extension)
            os.replace(str(staged), str(final))
            self.prune()
            return final
        except BaseException:
            staged.unlink(missing_ok=True)
            raise

    def prune(self):
        """Delete anything that is neither the current nor the previous image."""
        if not self.directory.is_dir():
            return
        keep = {self.CURRENT, self.PREVIOUS}
        for path in sorted(self.directory.iterdir()):
            if not path.is_file() or path.is_symlink():
                continue
            stem = path.name.split('.')[0]
            if stem not in keep:
                path.unlink(missing_ok=True)

    def clear(self):
        """Remove every cached image (used by `uninstall --purge`)."""
        if not self.directory.is_dir():
            return []
        removed = []
        for path in sorted(self.directory.iterdir()):
            if path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)
                removed.append(path.name)
        return removed
