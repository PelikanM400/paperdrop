"""The log budget: PaperDrop must not be able to fill a disk.

Two things bound it. PaperDrop's own log rotates. The files macOS writes for the
scheduled runs are owned by launchd, which never rotates them, so the client
shrinks them itself — and it has to do that by overwriting in place, because
launchd holds its own descriptor and a replaced file would keep receiving the
output while looking empty.
"""
from __future__ import annotations

import pathlib
import tempfile
import unittest

import support  # noqa: F401

from paperdrop.journal import TRUNCATION_NOTE, build_logger, shrink_file


class ShrinkFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / 'app.out.log'

    def write_lines(self, count, width=40):
        line = b'x' * width + b'\n'
        self.path.write_bytes(line * count)
        return self.path.stat().st_size

    def test_a_file_under_the_limit_is_left_alone(self):
        self.write_lines(10)
        before = self.path.read_bytes()
        self.assertEqual(shrink_file(self.path, 100000), 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_file_over_the_limit_keeps_its_tail(self):
        size = self.write_lines(100)
        self.assertGreater(size, 1000)
        self.assertEqual(shrink_file(self.path, 1000), size)
        after = self.path.read_bytes()
        self.assertLessEqual(len(after), 1000 + len(TRUNCATION_NOTE))
        self.assertTrue(after.startswith(TRUNCATION_NOTE))
        self.assertGreaterEqual(after.count(b'\n'), 10, 'the recent lines are kept')

    def test_shrinking_keeps_whole_lines(self):
        self.path.write_bytes(b'first line\n' + b'y' * 5000 + b'\nlast line\n')
        shrink_file(self.path, 200)
        lines = self.path.read_bytes().decode().splitlines()
        self.assertEqual(lines[0], TRUNCATION_NOTE.decode().strip())
        for line in lines[1:]:
            self.assertIn(line, ('y' * 5000, 'last line'),
                          'kept a partial line: %r' % line[:20])
        self.assertIn('last line', lines, 'the newest content survives')

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual(shrink_file(self.path, 10), 0)

    def test_repeated_shrinking_stays_bounded(self):
        # A bug spamming the log must not be able to grow the file without limit.
        limit = 4096
        for _ in range(50):
            with open(self.path, 'ab') as handle:
                handle.write(b'z' * 2000 + b'\n')
            shrink_file(self.path, limit)
            self.assertLessEqual(self.path.stat().st_size, limit + len(TRUNCATION_NOTE))

    def test_an_appending_writer_continues_at_the_new_end(self):
        # launchd keeps the file open with O_APPEND; the file must stay usable.
        self.write_lines(100)
        handle = open(self.path, 'ab')
        self.addCleanup(handle.close)
        shrink_file(self.path, 500)
        handle.write(b'after the shrink\n')
        handle.flush()
        self.assertTrue(self.path.read_bytes().endswith(b'after the shrink\n'))
        self.assertLess(self.path.stat().st_size, 600)

    def test_the_rotating_log_is_bounded_by_max_bytes_and_keep(self):
        path = pathlib.Path(self.directory.name) / 'paperdrop.log'
        logger = build_logger(path, max_bytes=2048, keep=2, console=False)
        for index in range(400):
            logger.info('a line of log output number %d', index)
        for handler in list(logger.handlers):
            handler.close()
        total = sum(item.stat().st_size for item in pathlib.Path(self.directory.name).iterdir())
        self.assertLessEqual(total, 2048 * 3, 'max_bytes * (keep + 1) is the ceiling')


if __name__ == '__main__':
    unittest.main(verbosity=2)
