"""Cache: atomic publish, current/previous retention, pruning."""
from __future__ import annotations

import pathlib
import tempfile
import unittest

import support

from paperdrop.cache import Cache
from paperdrop.util import sha256_hex


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)
        self.cache = Cache(self.root / 'cache')

    def test_publish_creates_the_current_image(self):
        path = self.cache.publish(b'first', '.jpg')
        self.assertEqual(path, self.cache.directory / 'current.jpg')
        self.assertEqual(path.read_bytes(), b'first')
        self.assertIsNone(self.cache.previous())

    def test_second_publish_demotes_the_previous_current(self):
        self.cache.publish(b'first', '.jpg')
        self.cache.publish(b'second', '.jpg')
        self.assertEqual(self.cache.current().read_bytes(), b'second')
        self.assertEqual(self.cache.previous().read_bytes(), b'first')

    def test_at_most_two_images_are_kept(self):
        for payload in (b'one', b'two', b'three', b'four'):
            self.cache.publish(payload, '.jpg')
        files = sorted(p.name for p in self.cache.directory.iterdir())
        self.assertEqual(files, ['current.jpg', 'previous.jpg'])
        self.assertEqual(self.cache.current().read_bytes(), b'four')
        self.assertEqual(self.cache.previous().read_bytes(), b'three')

    def test_changing_extension_replaces_rather_than_accumulates(self):
        self.cache.publish(b'jpeg', '.jpg')
        self.cache.publish(b'png', '.png')
        names = sorted(p.name for p in self.cache.directory.iterdir())
        self.assertEqual(names, ['current.png', 'previous.jpg'])
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
        self.assertEqual([p.name for p in self.cache.directory.iterdir()], ['current.jpg'])

    def test_clear_removes_cached_images(self):
        self.cache.publish(b'first', '.jpg')
        self.cache.publish(b'second', '.jpg')
        removed = self.cache.clear()
        self.assertEqual(sorted(removed), ['current.jpg', 'previous.jpg'])
        self.assertEqual(list(self.cache.directory.iterdir()), [])

    def test_permissions_are_private(self):
        self.cache.publish(b'private', '.jpg')
        mode = self.cache.current().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == '__main__':
    unittest.main(verbosity=2)
