"""Cache: atomic publish, current/previous retention, pruning."""
from __future__ import annotations

import pathlib
import tempfile
import unittest

import support

from paperdrop.cache import Cache
from paperdrop.errors import PaperDropError
from paperdrop.util import sha256_hex


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)
        self.cache = Cache(self.root / 'cache')

    def test_publish_creates_the_current_image(self):
        path = self.cache.publish(b'first', '.jpg')
        self.assertEqual(path, self.cache.directory / ('image-' + sha256_hex(b'first') + '.jpg'))
        self.assertEqual(path.read_bytes(), b'first')
        self.assertIsNone(self.cache.previous())

    def test_second_publish_demotes_the_previous_current(self):
        self.cache.publish(b'first', '.jpg')
        self.cache.publish(b'second', '.jpg')
        self.assertEqual(self.cache.current().read_bytes(), b'second')
        self.assertEqual(self.cache.previous().read_bytes(), b'first')

    def test_identical_bytes_with_extension_drift_do_not_evict_previous(self):
        first = self.cache.publish(b'A', '.jpg')
        second = self.cache.publish(b'B', '.jpg')
        inode = second.stat().st_ino
        repeated = self.cache.publish(b'B', '.jpeg')
        self.assertEqual(repeated, second, 'identity is verified bytes, not a role or extension')
        self.assertEqual(repeated.stat().st_ino, inode)
        self.assertEqual(self.cache.previous(), first)
        self.assertEqual({p for p in self.cache.directory.iterdir() if p.name != Cache.INDEX},
                         {first, second})
        # Reuse a retained immutable URL when A returns, without renaming B.
        self.assertEqual(self.cache.publish(b'A', '.jpg'), first)
        self.assertEqual(self.cache.previous(), second)
        third = self.cache.publish(b'C', '.png')
        self.assertFalse(second.exists())
        self.assertEqual({p for p in self.cache.directory.iterdir() if p.name != Cache.INDEX},
                         {first, third})

    def test_a_legacy_symlink_role_is_refused_before_anything_is_pruned(self):
        """v0.1.6 names may be links; ignoring one silently loses real image data.

        Filtering a symlinked previous.* out of the legacy roles made migration
        commit previous=null and then prune the regular file the link targeted.
        """
        self.cache.ensure()
        current = self.cache.directory / 'current.jpg'
        current.write_bytes(b'A')
        target = self.cache.directory / ('image-' + sha256_hex(b'B') + '.jpg')
        target.write_bytes(b'B')
        (self.cache.directory / 'previous.jpg').symlink_to(target)

        with self.assertRaises(PaperDropError):
            self.cache.migrate()

        self.assertTrue(target.exists(), 'the file the legacy symlink pointed at must survive')
        self.assertTrue(current.exists())
        self.assertFalse((self.cache.directory / Cache.INDEX).exists(),
                         'no index may be committed around an ambiguous legacy role')

    def test_a_non_regular_legacy_role_is_also_refused(self):
        self.cache.ensure()
        (self.cache.directory / 'current.jpg').mkdir()
        previous = self.cache.directory / 'previous.jpg'
        previous.write_bytes(b'B')

        with self.assertRaises(PaperDropError):
            self.cache.migrate()

        self.assertTrue(previous.exists())
        self.assertFalse((self.cache.directory / Cache.INDEX).exists())

    def test_restart_prunes_unindexed_images_left_by_interrupted_publish(self):
        current = self.cache.publish(b'A', '.jpg')
        previous = self.cache.publish(b'B', '.jpg')
        orphan = self.cache.directory / ('image-' + sha256_hex(b'C') + '.jpg')
        orphan.write_bytes(b'C')  # death after immutable file creation, before index commit
        interrupted = self.cache.directory / '.incoming-interrupted.jpg'
        interrupted.write_bytes(b'partial')
        restarted = Cache(self.cache.directory)
        restarted.migrate()
        self.assertEqual(set(restarted.directory.iterdir()),
                         {current, previous, restarted.directory / Cache.INDEX})
        self.assertEqual(restarted.current(), previous)
        self.assertEqual(restarted.previous(), current)

    def test_at_most_two_images_are_kept(self):
        for payload in (b'one', b'two', b'three', b'four'):
            self.cache.publish(payload, '.jpg')
        files = {p for p in self.cache.directory.iterdir() if p.name != Cache.INDEX}
        self.assertEqual(files, {self.cache.current(), self.cache.previous()})
        self.assertEqual(self.cache.current().read_bytes(), b'four')
        self.assertEqual(self.cache.previous().read_bytes(), b'three')

    def test_changing_extension_replaces_rather_than_accumulates(self):
        self.cache.publish(b'jpeg', '.jpg')
        self.cache.publish(b'png', '.png')
        images = {p for p in self.cache.directory.iterdir() if p.name != Cache.INDEX}
        self.assertEqual(images, {self.cache.current(), self.cache.previous()})
        self.assertEqual(self.cache.previous().suffix, '.jpg')
        self.assertEqual(self.cache.current().suffix, '.png')

    def test_no_temp_file_survives_a_publish(self):
        self.cache.publish(b'payload', '.jpg')
        self.assertEqual([p.name for p in self.cache.directory.iterdir() if p.name.startswith('.')], [])

    def test_publish_is_atomic_so_the_current_file_is_never_partial(self):
        self.cache.publish(support.make_image((40, 30)), '.jpg')
        path = self.cache.current()
        self.assertEqual(sha256_hex(path.read_bytes()), sha256_hex(support.make_image((40, 30))))
        self.assertTrue(path.is_file())

    def test_prune_removes_foreign_files(self):
        self.cache.ensure()
        (self.cache.directory / 'stray.jpg').write_bytes(b'x')
        self.cache.publish(b'kept', '.jpg')
        self.assertEqual(set(self.cache.directory.iterdir()),
                         {self.cache.current(), self.cache.directory / Cache.INDEX})

    def test_clear_removes_cached_images(self):
        self.cache.publish(b'first', '.jpg')
        self.cache.publish(b'second', '.jpg')
        expected = {self.cache.current().name, self.cache.previous().name, Cache.INDEX}
        removed = self.cache.clear()
        self.assertEqual(set(removed), expected)
        self.assertEqual(list(self.cache.directory.iterdir()), [])

    def test_legacy_read_fallback_and_migration_preserve_both_payloads(self):
        self.cache.ensure()
        current = self.cache.directory / 'current.png'
        previous = self.cache.directory / 'previous.jpg'
        current.write_bytes(b'current')
        previous.write_bytes(b'previous')
        current_inode = current.stat().st_ino
        self.assertEqual(self.cache.current(), current)
        self.assertEqual(self.cache.previous(), previous)
        self.assertFalse((self.cache.directory / Cache.INDEX).exists())
        self.cache.migrate()
        self.assertEqual(self.cache.current().read_bytes(), b'current')
        self.assertEqual(self.cache.previous().read_bytes(), b'previous')
        self.assertEqual(self.cache.current().stat().st_ino, current_inode)
        self.assertEqual(self.cache.current().name, 'image-' + sha256_hex(b'current') + '.png')
        self.assertEqual(len(list(self.cache.directory.iterdir())), 3)

    def test_failed_migration_index_commit_keeps_legacy_names_and_bytes(self):
        self.cache.ensure()
        current = self.cache.directory / 'current.jpg'
        previous = self.cache.directory / 'previous.png'
        current.write_bytes(b'A')
        previous.write_bytes(b'B')
        from unittest.mock import patch
        with patch.object(self.cache, '_commit', side_effect=OSError('index write failed')):
            with self.assertRaises(OSError):
                self.cache.migrate()
        self.assertEqual(set(self.cache.directory.iterdir()), {current, previous})
        self.assertEqual(current.read_bytes(), b'A')
        self.assertEqual(previous.read_bytes(), b'B')

    def test_failed_publish_index_commit_keeps_both_indexed_images(self):
        first = self.cache.publish(b'A', '.jpg')
        second = self.cache.publish(b'B', '.png')
        index = (self.cache.directory / Cache.INDEX).read_bytes()
        from unittest.mock import patch
        with patch.object(self.cache, '_commit', side_effect=OSError('index write failed')):
            with self.assertRaises(OSError):
                self.cache.publish(b'C', '.jpg')
        self.assertEqual(self.cache.current(), second)
        self.assertEqual(self.cache.previous(), first)
        self.assertEqual(first.read_bytes(), b'A')
        self.assertEqual(second.read_bytes(), b'B')
        self.assertEqual((self.cache.directory / Cache.INDEX).read_bytes(), index)
        self.assertEqual(len(list(self.cache.directory.iterdir())), 3)

    def test_damaged_immutable_url_is_rejected_never_overwritten(self):
        from paperdrop.errors import PaperDropError
        path = self.cache.publish(b'A', '.jpg')
        path.write_bytes(b'corrupted externally')
        with self.assertRaises(PaperDropError):
            self.cache.publish(b'A', '.jpg')
        self.assertEqual(path.read_bytes(), b'corrupted externally')

    def test_invalid_index_does_not_trigger_destructive_legacy_fallback(self):
        from paperdrop.errors import PaperDropError
        path = self.cache.publish(b'A', '.jpg')
        (self.cache.directory / Cache.INDEX).write_text('{broken', encoding='utf-8')
        with self.assertRaises(PaperDropError):
            self.cache.migrate()
        self.assertEqual(path.read_bytes(), b'A')

    def test_invalid_role_targets_are_rejected_before_any_pruning(self):
        import json
        from paperdrop.errors import PaperDropError
        for role in ('current', 'previous', 'active'):
            for kind in ('missing', 'symlink', 'directory'):
                with self.subTest(role=role, kind=kind):
                    cache = Cache(self.root / (role + '-' + kind))
                    active = cache.publish(b'A', '.jpg')
                    target = cache.directory / ('image-' + '0' * 64 + '.jpg')
                    if kind == 'symlink':
                        target.symlink_to(active)
                    elif kind == 'directory':
                        target.mkdir()
                    index = cache.directory / Cache.INDEX
                    roles = {'version': 1, 'current': active.name, 'previous': None,
                             'active': active.name}
                    roles[role] = target.name
                    index.write_text(json.dumps(roles))
                    before = set(cache.directory.iterdir())
                    for operation in (cache.current, cache.previous, cache.migrate, cache.prune):
                        with self.assertRaises(PaperDropError):
                            operation()
                        self.assertEqual(set(cache.directory.iterdir()), before)
                        self.assertEqual(active.read_bytes(), b'A')

    def test_permissions_are_private(self):
        self.cache.publish(b'private', '.jpg')
        mode = self.cache.current().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == '__main__':
    unittest.main(verbosity=2)
