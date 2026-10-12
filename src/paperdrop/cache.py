"""Immutable image URLs with an atomic current/previous role index.

Completed operations retain at most two images. Staged files are private and
never handed to a desktop; role changes never rename or overwrite image URLs.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import secrets

from .errors import PaperDropError
from .util import atomic_write, ensure_dir, sha256_hex


class Cache:
    CURRENT = 'current'
    PREVIOUS = 'previous'
    ACTIVE = 'active'
    INDEX = 'images.json'
    IMAGE_NAME = re.compile(r'^image-[0-9a-f]{64}\.[a-zA-Z0-9]+$')

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
        """The legacy fixed-name role, refusing anything but a regular file.

        A pre-index cache may hold a symlink (or a directory) under current.* /
        previous.*. Silently skipping it made migration commit that role as
        absent and then prune the very image the entry pointed at, so an
        ambiguous legacy entry is a hard error instead: nothing is migrated and
        nothing is pruned around it.
        """
        if not self.directory.is_dir():
            return None
        entries = sorted(self.directory.glob(stem + '.*'))
        for entry in entries:
            if entry.is_symlink() or not entry.is_file():
                raise PaperDropError(
                    'Legacy cache entry %s is not a regular file; refusing to migrate '
                    'or prune around it.' % entry.name)
        return entries[0] if entries else None

    def _roles(self):
        index = self.directory / self.INDEX
        if not index.exists() and not index.is_symlink():
            roles = {role: self._find(role) for role in (self.CURRENT, self.PREVIOUS)}
            roles[self.ACTIVE] = roles[self.CURRENT]
            return roles
        try:
            if index.is_symlink():
                raise ValueError('image index must not be a symlink')
            value = json.loads(index.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or value.get('version') != 1:
                raise ValueError('unsupported image index')
            roles = {}
            for role in (self.CURRENT, self.PREVIOUS, self.ACTIVE):
                name = value.get(role, value[self.CURRENT]) if role == self.ACTIVE else value[role]
                if name is not None and not self.IMAGE_NAME.fullmatch(name):
                    raise ValueError('invalid image name')
                roles[role] = self.directory / name if name else None
                path = roles[role]
                if path is not None and (path.is_symlink() or not path.is_file()):
                    raise ValueError('%s image must be an existing regular non-symlink file' % role)
            return roles
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise PaperDropError('Could not read the cache image index: %s' % error) from error

    def current(self):
        return self._roles()[self.CURRENT]

    def previous(self):
        return self._roles()[self.PREVIOUS]

    def staged_path(self, suffix):
        """A private temp name in the cache directory (same filesystem)."""
        self.ensure()
        return self.directory / ('.incoming-' + secrets.token_hex(6) + suffix)

    def _store(self, data, extension):
        extension = extension if extension.startswith('.') else '.' + extension
        if not re.fullmatch(r'\.[a-zA-Z0-9]+', extension):
            raise ValueError('invalid image extension')
        prefix = 'image-' + sha256_hex(data)
        # A repeat (including extension drift) must keep its original URL.
        retained = sorted(p for p in self.directory.glob(prefix + '.*')
                          if self.IMAGE_NAME.fullmatch(p.name))
        final = retained[0] if retained else self.directory / (prefix + extension)
        if final.exists() or final.is_symlink():
            if final.is_symlink() or final.read_bytes() != data:
                raise PaperDropError('Immutable cached image is damaged: %s' % final)
            return final
        staged = self.staged_path(extension)
        try:
            atomic_write(staged, data, mode=0o600)
            # Never replace an existing image URL, even when roles change.
            os.link(str(staged), str(final))
        except BaseException:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass  # Keep the original staging/link failure.
            raise
        else:
            staged.unlink(missing_ok=True)
        return final

    def _commit(self, current, previous, active='current'):
        active = current if active == 'current' else active
        value = {'version': 1, self.CURRENT: current.name if current else None,
                 self.PREVIOUS: previous.name if previous else None,
                 self.ACTIVE: active.name if active else None}
        atomic_write(self.directory / self.INDEX,
                     (json.dumps(value, indent=2) + '\n').encode('utf-8'), mode=0o600)

    def migrate(self):
        """Upgrade fixed v0.1.6 names without losing either role's bytes.

        Hard links avoid copying image data. Legacy names remain available until
        both immutable URLs exist and one atomic index commits; only then prune.
        """
        if (self.directory / self.INDEX).exists() or (self.directory / self.INDEX).is_symlink():
            roles = self._roles()
            if roles[self.ACTIVE] != roles[self.CURRENT]:
                self.retain_current(roles[self.ACTIVE])
            self.prune()
            return self.current()
        roles = self._roles()
        if not any(roles.values()):
            return None
        self.ensure()
        migrated = {}
        try:
            for role in (self.CURRENT, self.PREVIOUS):
                path = roles[role]
                if path is None:
                    migrated[role] = None
                    continue
                data = path.read_bytes()
                final = self.directory / ('image-' + sha256_hex(data) + path.suffix)
                if final.exists() or final.is_symlink():
                    if final.is_symlink() or final.read_bytes() != data:
                        raise PaperDropError('Immutable cached image is damaged: %s' % final)
                else:
                    os.link(str(path), str(final))
                migrated[role] = final
            self._commit(migrated[self.CURRENT], migrated[self.PREVIOUS])
        except BaseException:
            try:
                self.prune()
            except (OSError, PaperDropError):
                pass  # Preserve the original failure, not a secondary cleanup error.
            raise
        self.prune()
        return migrated[self.CURRENT]

    def publish(self, data, extension, confirmed=True):
        """Publish verified bytes; promote an existing URL without changing it."""
        self.ensure()
        self.migrate()
        roles = self._roles()
        try:
            final = self._store(data, extension)
            old_current = roles[self.ACTIVE]
            previous = roles[self.PREVIOUS] if final == old_current else old_current
            self._commit(final, previous, active=final if confirmed else old_current)
        except BaseException:
            try:
                self.prune()
            except (OSError, PaperDropError):
                pass  # Preserve the original failure, not a secondary cleanup error.
            raise
        self.prune()
        return final

    def retain_current(self, path):
        """On apply failure keep the old active URL as current, not a failed update.

        The rejected (but verified) image becomes previous. This also prevents
        repeated failures from eventually pruning the wallpaper still in use.
        """
        roles = self._roles()
        if path == roles[self.ACTIVE] and path != roles[self.CURRENT]:
            self._commit(path, roles[self.CURRENT])
            self.prune()

    def confirm_current(self, path):
        """Advance the durable active URL only after bounded desktop confirmation."""
        roles = self._roles()
        if path != roles[self.CURRENT]:
            raise PaperDropError('Confirmed image is not the current cache candidate')
        self._commit(path, roles[self.PREVIOUS])
        self.prune()

    def prune(self):
        """Prune only after the atomic index commits; retain both indexed URLs."""
        if not self.directory.is_dir():
            return
        keep = {p for p in self._roles().values() if p is not None}
        keep.add(self.directory / self.INDEX)
        for path in sorted(self.directory.iterdir()):
            if path.is_file() and not path.is_symlink() and path not in keep:
                path.unlink(missing_ok=True)

    def clear(self):
        """Remove cached images and their index (used by `uninstall --purge`)."""
        if not self.directory.is_dir():
            return []
        removed = []
        for path in sorted(self.directory.iterdir()):
            if path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)
                removed.append(path.name)
        return removed
