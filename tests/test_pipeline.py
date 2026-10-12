"""End-to-end runs with a fake transport: caching, no-op runs, failure handling."""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import support

from paperdrop import state as state_module
from paperdrop.cache import Cache
from paperdrop.errors import FetchError, PlatformError
from paperdrop.fetch import Response
from paperdrop.paths import Paths
from paperdrop.pipeline import apply_cached, run_once, screen_poll_loop
from paperdrop.state import State
from paperdrop.util import sha256_hex

MANIFEST_URL = 'https://example.test/desk.json'
IMAGE_URL = 'https://example.test/commit/desk.jpg'
NOW = datetime.datetime(2030, 1, 2, 4, 30, tzinfo=datetime.timezone.utc)


class PathCachingDesktop(support.FakePlatform):
    """A deterministic model of AppKit's same-URL/different-bytes hazard."""
    def __init__(self):
        super().__init__()
        self.pixels_by_path = {}
        self.visible = None

    def apply(self, image_path):
        result = super().apply(image_path)
        path = str(image_path)
        if path not in self.pixels_by_path:
            self.pixels_by_path[path] = pathlib.Path(path).read_bytes()
        self.visible = self.pixels_by_path[path]
        return result


class _Collect(logging.Handler):
    """Keeps the log lines a test wants to assert on."""

    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def emit(self, record):
        self.sink.append(record.getMessage())


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)
        self.image = support.make_image((640, 360))
        self.manifest = support.manifest_for(self.image, image_url=IMAGE_URL,
                                             width=640, height=360)
        self.logger = support.silent_logger()

    def build(self, mode='manifest', url=MANIFEST_URL, **overrides):
        config, config_path = support.write_config(self.root, url, mode=mode, **overrides)
        paths = Paths.from_config(config, config_file=config_path)
        responses = {MANIFEST_URL: Response(url=MANIFEST_URL, status=200, data=self.manifest,
                                            headers={'Content-Type': 'application/json'}),
                     IMAGE_URL: Response(url=IMAGE_URL, status=200, data=self.image,
                                         headers={'Content-Type': 'image/jpeg', 'ETag': '"v1"'})}
        fetcher = support.FakeFetcher(responses)
        return config, paths, fetcher

    def run_once(self, config, paths, fetcher, platform=None, **kwargs):
        platform = platform or support.FakePlatform()
        kwargs.setdefault('now', NOW)
        kwargs.setdefault('logger', self.logger)
        kwargs.setdefault('sleep', lambda seconds: None)   # tests never really wait
        return run_once(config, paths, platform, fetcher=fetcher, **kwargs), platform

    def test_the_original_error_survives_a_failed_failure_record(self):
        """A state-write error while reporting a failure must not replace it."""
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        first, _ = self.run_once(config, paths, fetcher, platform=platform)
        self.assertEqual(first.outcome, 'updated')

        other = support.make_image((640, 360), color='blue')
        fetcher.responses[MANIFEST_URL] = Response(
            url=MANIFEST_URL, status=200,
            data=support.manifest_for(other, image_url=IMAGE_URL, width=640, height=360),
            headers={'Content-Type': 'application/json'})
        fetcher.responses[IMAGE_URL] = Response(url=IMAGE_URL, status=200, data=other)

        real_write_json = state_module.write_json
        writes = []

        def flaky_write_json(path, payload):
            writes.append(path)
            if len(writes) == 1:
                raise OSError('SECONDARY failure-record write')
            return real_write_json(path, payload)

        with patch.object(Cache, '_store', side_effect=OSError('PRIMARY image store error')):
            with patch.object(state_module, 'write_json', side_effect=flaky_write_json):
                second, _ = self.run_once(config, paths, fetcher, platform=platform, force=True)

        self.assertEqual(second.outcome, 'failed')
        self.assertIn('PRIMARY', second.detail)
        attempt = State(paths.state_file).load().last_attempt
        self.assertIn('PRIMARY', attempt['reason'],
                      'the persisted reason must name the real failure, not the reporting error')

    def test_changed_bytes_use_a_new_path_on_a_path_caching_desktop(self):
        """Model pathname-cached pixels, not actual macOS visual confirmation."""
        config, paths, fetcher = self.build()
        platform = PathCachingDesktop()
        first, _ = self.run_once(config, paths, fetcher, platform=platform)
        other = support.make_image((640, 360), color='blue')
        fetcher.responses[MANIFEST_URL] = Response(
            url=MANIFEST_URL, status=200,
            data=support.manifest_for(other, image_url=IMAGE_URL, width=640, height=360),
            headers={'Content-Type': 'application/json'})
        fetcher.responses[IMAGE_URL] = Response(url=IMAGE_URL, status=200, data=other)
        second, _ = self.run_once(config, paths, fetcher, platform=platform, force=True)
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(platform.visible, other, 'same pathname leaves the old pixels visible')
        self.assertNotEqual(first.cache_path, second.cache_path)
        self.assertEqual(pathlib.Path(first.cache_path).read_bytes(), self.image,
                         'demoting a role must not rename or mutate its image URL')

    def test_upgrade_repairs_a_same_day_fixed_path_false_success_offline(self):
        for check_only in (False, True):
            with self.subTest(check_only=check_only):
                config, paths, fetcher = self.build()
                cache = Cache(paths.cache_dir)
                cache.clear()
                cache.ensure()
                legacy = cache.directory / 'current.jpg'
                older = support.make_image((640, 360), color='green')
                legacy.write_bytes(older)
                (cache.directory / 'previous.jpg').write_bytes(older)
                platform = PathCachingDesktop()
                platform.apply(legacy)
                # v0.1.6 replaced the bytes, but kept the same AppKit URL.
                legacy.write_bytes(self.image)
                State(paths.state_file).record_success(
                    identity=sha256_hex(self.image), image_sha256=sha256_hex(self.image),
                    cache_path=str(legacy), source_url=MANIFEST_URL, mode='manifest',
                    local_date=NOW.date().isoformat())
                result, _ = self.run_once(config, paths, fetcher, platform=platform,
                                          check_only=check_only)
                self.assertEqual(platform.visible, self.image)
                self.assertEqual(result.outcome, 'reapplied')
                self.assertEqual(fetcher.requests, [])
                self.assertFalse(legacy.exists())
                self.assertEqual(cache.previous().read_bytes(), older)
                self.assertEqual(cache.current().read_bytes(), self.image)
                self.assertEqual(State(paths.state_file).load().last_success['cache_path'],
                                 str(cache.current()))
                before = platform.apply_calls
                again, _ = self.run_once(config, paths, fetcher, platform=platform,
                                         check_only=check_only)
                self.assertEqual(again.outcome, 'unchanged')
                self.assertEqual(platform.apply_calls, before)
                self.assertEqual(State(paths.state_file).load().last_attempt['outcome'],
                                 'unchanged')

    def test_upgrade_apply_rejection_is_a_recorded_failure_not_an_exception(self):
        config, paths, fetcher = self.build()
        cache = Cache(paths.cache_dir)
        cache.ensure()
        legacy = cache.directory / 'current.jpg'
        legacy.write_bytes(self.image)
        State(paths.state_file).record_success(
            identity=sha256_hex(self.image), image_sha256=sha256_hex(self.image),
            cache_path=str(legacy), source_url=MANIFEST_URL, mode='manifest',
            local_date=NOW.date().isoformat())
        platform = support.FakePlatform(reports=str(legacy))
        platform.fail_with = PlatformError('AppKit rejected repair')
        try:
            result, _ = self.run_once(config, paths, fetcher, platform=platform, check_only=True)
        except PlatformError as error:
            self.fail('upgrade repair must record its failed attempt: %s' % error)
        self.assertEqual(result.outcome, 'failed')
        self.assertEqual(fetcher.requests, [])
        self.assertEqual(cache.current().read_bytes(), self.image)
        state = State(paths.state_file).load()
        self.assertFalse(state.last_attempt['ok'])
        self.assertEqual(state.last_success['cache_path'], str(legacy))
        self.assertEqual(platform.current(), str(legacy))

    def test_a_desktop_that_reports_the_old_image_is_retried(self):
        """The confirmation must not give up on the first stale read."""
        config, paths, fetcher = self.build()
        platform = support.SlowReportingPlatform(lag=3)
        waited = []
        result, platform = self.run_once(config, paths, fetcher, platform=platform,
                                         sleep=waited.append)
        self.assertEqual(result.outcome, 'updated')
        self.assertTrue(result.applied)
        self.assertEqual(result.checks, ['desktop_state'])
        self.assertTrue(waited, 'it should have waited before polling again')
        self.assertEqual(State(paths.state_file).load().last_success['image_sha256'],
                         sha256_hex(self.image))

    def test_a_desktop_that_never_reports_the_new_image_still_fails(self):
        config, paths, fetcher = self.build()
        platform = support.FakePlatform(reports='/other.png', apply_effective=False)
        waited = []
        result, _ = self.run_once(config, paths, fetcher, platform=platform,
                                  sleep=waited.append)
        self.assertEqual(result.outcome, 'failed')
        self.assertIn('could not be confirmed', result.detail)
        self.assertTrue(waited, 'it should have retried before giving up')
        self.assertIsNone(State(paths.state_file).load().last_success)

    # --- happy path ---------------------------------------------------------
    def test_first_run_downloads_verifies_caches_and_applies(self):
        config, paths, fetcher = self.build()
        result, platform = self.run_once(config, paths, fetcher)
        self.assertEqual(result.outcome, 'updated')
        self.assertTrue(result.downloaded)
        self.assertTrue(result.applied)
        cached = Cache(paths.cache_dir).current()
        self.assertEqual(cached.read_bytes(), self.image)
        self.assertEqual(platform.applied, [str(cached)])
        state = State(paths.state_file).load()
        self.assertEqual(state.last_success['image_sha256'], sha256_hex(self.image))
        self.assertEqual(state.last_success['identity'], sha256_hex(self.image))
        self.assertFalse(result.wrote_remote, 'PaperDrop must never publish anything')

    def test_a_second_run_the_same_day_touches_neither_the_network_nor_the_desktop(self):
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        self.run_once(config, paths, fetcher, platform=platform)
        fetcher.requests.clear()
        result, platform = self.run_once(config, paths, fetcher, platform=platform)
        self.assertEqual(result.outcome, 'unchanged')
        self.assertEqual(fetcher.requests, [], 'a same-day repeat must not fetch anything')
        self.assertEqual(platform.apply_calls, 1, 'the wallpaper must not be re-applied')
        self.assertIn('desktop_state', result.checks)

    def test_a_same_day_run_still_leaves_a_trace_that_it_happened(self):
        # A run that finds today's image already in place used to touch nothing on
        # disk, so "did the schedule fire?" could not be answered at all.
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        first, _ = self.run_once(config, paths, fetcher, platform=platform)
        self.assertEqual(first.outcome, 'updated')
        after_success = State(paths.state_file).load()
        self.assertIsNone(after_success.last_attempt.get('outcome'),
                          'a real success is recorded as a success, not as a no-op')

        second, _ = self.run_once(config, paths, fetcher, platform=platform)
        self.assertEqual(second.outcome, 'unchanged')
        after_noop = State(paths.state_file).load()
        self.assertTrue(after_noop.last_attempt['ok'])
        self.assertEqual(after_noop.last_attempt['outcome'], 'unchanged')
        self.assertTrue(after_noop.last_attempt['finished_at'])
        self.assertEqual(after_noop.last_success['finished_at'],
                         after_success.last_success['finished_at'],
                         '"last ok" must keep meaning the last time it changed')

    def test_changing_the_source_url_does_not_silently_keep_todays_image(self):
        """Switching sources must be picked up, not masked by the same-day shortcut."""
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        self.run_once(config, paths, fetcher, platform=platform)
        fetcher.requests.clear()

        other_url = 'https://example.test/other.json'
        other_image = support.make_image((640, 360), color='blue')
        fetcher.responses[other_url] = Response(
            url=other_url, status=200,
            data=support.manifest_for(other_image, image_url='https://example.test/other.jpg',
                                      width=640, height=360),
            headers={'Content-Type': 'application/json'})
        fetcher.responses['https://example.test/other.jpg'] = Response(
            url='https://example.test/other.jpg', status=200, data=other_image,
            headers={'Content-Type': 'image/jpeg'})
        config['source']['url'] = other_url

        result, platform = self.run_once(config, paths, fetcher, platform=platform)
        self.assertNotEqual(result.outcome, 'unchanged',
                            'a new source must not be hidden behind "already up to date"')
        self.assertTrue(result.downloaded)
        self.assertEqual(fetcher.requests[0], ('GET', other_url))
        self.assertEqual(Cache(paths.cache_dir).current().read_bytes(), other_image)
        self.assertEqual(State(paths.state_file).load().last_success['source_url'], other_url)

    def test_matching_sha_in_cache_skips_the_download_but_still_applies(self):
        config, paths, fetcher = self.build()
        Cache(paths.cache_dir).publish(self.image, '.jpg')
        result, platform = self.run_once(config, paths, fetcher)
        self.assertFalse(result.downloaded)
        self.assertTrue(result.applied)
        self.assertEqual([call for call in fetcher.requests if call[1] == IMAGE_URL], [])
        self.assertEqual(platform.applied, [str(Cache(paths.cache_dir).current())])

    def test_switching_to_a_source_with_the_same_image_explains_itself(self):
        """Two mirrors of the same image: no download, but say why."""
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        self.run_once(config, paths, fetcher, platform=platform)
        mirror = 'https://example.test/mirror.json'
        fetcher.responses[mirror] = Response(
            url=mirror, status=200,
            data=support.manifest_for(self.image, image_url=IMAGE_URL,
                                      width=640, height=360),
            headers={'Content-Type': 'application/json'})
        config['source']['url'] = mirror

        result, platform = self.run_once(config, paths, fetcher, platform=platform)
        self.assertFalse(result.downloaded, 'the same bytes are already cached')
        self.assertIn('source changed', result.detail)
        self.assertEqual(State(paths.state_file).load().last_success['source_url'], mirror)

    def test_force_redownloads_on_the_same_day(self):
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        self.run_once(config, paths, fetcher, platform=platform)
        result, platform = self.run_once(config, paths, fetcher, platform=platform, force=True)
        self.assertEqual(result.outcome, 'updated')
        self.assertTrue(result.downloaded)
        self.assertEqual(platform.apply_calls, 2)

    # --- failure handling ---------------------------------------------------
    def test_sha_mismatch_keeps_the_previous_wallpaper(self):
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        self.run_once(config, paths, fetcher, platform=platform)
        previous = Cache(paths.cache_dir).current().read_bytes()
        fetcher.responses[MANIFEST_URL] = Response(
            url=MANIFEST_URL, status=200,
            data=support.manifest_for(self.image, image_url=IMAGE_URL, sha256='0' * 64),
            headers={'Content-Type': 'application/json'})
        result, platform = self.run_once(config, paths, fetcher, platform=platform, force=True)
        self.assertEqual(result.outcome, 'failed')
        self.assertIn('SHA-256 mismatch', result.detail)
        self.assertEqual(Cache(paths.cache_dir).current().read_bytes(), previous)
        self.assertEqual(platform.apply_calls, 1, 'the desktop must be left alone on failure')

    def test_undecodable_body_is_rejected_even_when_the_hash_matches(self):
        config, paths, fetcher = self.build()
        not_an_image = b'<html>totally not an image</html>' * 200
        fetcher.responses[MANIFEST_URL] = Response(
            url=MANIFEST_URL, status=200,
            data=support.manifest_for(not_an_image, image_url=IMAGE_URL),
            headers={'Content-Type': 'application/json'})
        fetcher.responses[IMAGE_URL] = Response(url=IMAGE_URL, status=200, data=not_an_image,
                                                headers={'Content-Type': 'text/html'})
        result, platform = self.run_once(config, paths, fetcher)
        self.assertEqual(result.outcome, 'failed')
        self.assertIn('image', result.detail.lower())
        self.assertEqual(platform.applied, [])

    def test_network_failure_keeps_the_previous_wallpaper(self):
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        self.run_once(config, paths, fetcher, platform=platform)
        previous = Cache(paths.cache_dir).current().read_bytes()
        fetcher.responses[MANIFEST_URL] = FetchError('connection reset', retryable=True)
        result, platform = self.run_once(config, paths, fetcher, platform=platform, force=True)
        self.assertEqual(result.outcome, 'failed')
        self.assertEqual(Cache(paths.cache_dir).current().read_bytes(), previous)
        self.assertEqual(platform.apply_calls, 1)

    def test_repeated_apply_failures_keep_the_old_wallpaper_file(self):
        config, paths, fetcher = self.build()
        platform = PathCachingDesktop()
        first, _ = self.run_once(config, paths, fetcher, platform=platform)
        original = pathlib.Path(first.cache_path)
        for color in ('blue', 'green'):
            other = support.make_image((640, 360), color=color)
            fetcher.responses[MANIFEST_URL] = Response(
                url=MANIFEST_URL, status=200,
                data=support.manifest_for(other, image_url=IMAGE_URL, width=640, height=360),
                headers={'Content-Type': 'application/json'})
            fetcher.responses[IMAGE_URL] = Response(url=IMAGE_URL, status=200, data=other)
            platform.fail_with = PlatformError('AppKit rejected the new image')
            result, _ = self.run_once(config, paths, fetcher, platform=platform, force=True)
            self.assertEqual(result.outcome, 'failed')
            self.assertEqual(platform.visible, self.image)
            self.assertTrue(original.exists(), 'failed updates must not prune the active URL')
            self.assertEqual(Cache(paths.cache_dir).current(), original)
            self.assertEqual(State(paths.state_file).load().last_success['cache_path'],
                             str(original))
            self.assertEqual(len(list(paths.cache_dir.glob('image-*'))), 2)

    def test_active_url_survives_failed_cleanup_or_rollback_then_next_publication(self):
        from unittest.mock import patch
        import paperdrop.cache as cache_module
        real_write = cache_module.atomic_write
        real_unlink = pathlib.Path.unlink
        for failure in ('postcommit-cleanup', 'rollback-index'):
            with self.subTest(failure=failure):
                self.root = pathlib.Path(self.directory.name) / failure
                self.root.mkdir()
                config, paths, fetcher = self.build()
                platform = PathCachingDesktop()
                first, _ = self.run_once(config, paths, fetcher, platform=platform)
                active = pathlib.Path(first.cache_path)
                orphan = paths.cache_dir / 'cleanup-failure.jpg'
                committed = []
                injected = []

                def write(path, data, **kwargs):
                    if pathlib.Path(path).name == Cache.INDEX:
                        value = json.loads(data)
                        if value['current'] != active.name:
                            committed.append(value)
                            if failure == 'postcommit-cleanup':
                                orphan.write_bytes(b'cleanup')
                        elif committed and failure == 'rollback-index':
                            injected.append(failure)
                            raise OSError('injected rollback index failure')
                    return real_write(path, data, **kwargs)

                def unlink(path, **kwargs):
                    if path == orphan and committed:
                        injected.append(failure)
                        raise OSError('injected postcommit cleanup failure')
                    return real_unlink(path, **kwargs)

                for color in ('blue', 'green'):
                    image = support.make_image((640, 360), color=color)
                    fetcher.responses[MANIFEST_URL] = Response(
                        url=MANIFEST_URL, status=200,
                        data=support.manifest_for(image, image_url=IMAGE_URL,
                                                  width=640, height=360))
                    fetcher.responses[IMAGE_URL] = Response(url=IMAGE_URL, status=200, data=image)
                    platform.fail_with = PlatformError('injected apply rejection')
                    # The first failure is inside real publication/rollback FS IO.
                    # The next run creates a fresh Cache and publishes another candidate.
                    if color == 'blue':
                        def get(url, **kwargs):
                            response = original_get(url, **kwargs)
                            if url == IMAGE_URL and failure == 'postcommit-cleanup':
                                orphan.write_bytes(b'cleanup')
                            return response
                        original_get = fetcher.get
                        with patch.object(fetcher, 'get', side_effect=get), \
                                patch('paperdrop.cache.atomic_write', side_effect=write), \
                                patch.object(pathlib.Path, 'unlink', unlink):
                            try:
                                result, _ = self.run_once(config, paths, fetcher,
                                                          platform=platform, force=True)
                            except OSError as error:
                                self.fail('filesystem failure must return a failed attempt: %s' % error)
                    else:
                        result, _ = self.run_once(config, paths, fetcher,
                                                  platform=platform, force=True)
                    self.assertEqual(result.exit_code, 1)
                    self.assertEqual(platform.visible, self.image)
                    self.assertTrue(active.exists(),
                                    'the next publication must not prune the still-visible A URL')
                    self.assertEqual(active.read_bytes(), self.image,
                                     'a subsequent failed candidate must not evict confirmed A')
                    self.assertEqual(State(paths.state_file).load().last_success['cache_path'],
                                     str(active))
                    self.assertFalse(State(paths.state_file).load().last_attempt['ok'])
                self.assertIn(failure, injected, 'must exercise the real injected filesystem failure')
                self.assertEqual(Cache(paths.cache_dir).current(), active)
                self.assertEqual(len(list(paths.cache_dir.glob('image-*'))), 2)
                platform.fail_with = None
                final, _ = self.run_once(config, paths, fetcher, platform=platform, force=True)
                self.assertEqual(final.exit_code, 0)
                self.assertTrue(active.exists())
                self.assertEqual(Cache(paths.cache_dir).previous(), active)

    def test_filesystem_failures_record_original_error_even_when_cleanup_fails(self):
        import os
        from unittest.mock import patch
        real_replace, real_link, real_unlink = os.replace, os.link, pathlib.Path.unlink
        for boundary in ('staging', 'link', 'index', 'migration', 'apply'):
            with self.subTest(boundary=boundary):
                self.root = pathlib.Path(self.directory.name) / boundary
                self.root.mkdir()
                config, paths, fetcher = self.build()
                platform = PathCachingDesktop()
                first, _ = self.run_once(config, paths, fetcher, platform=platform)
                active = pathlib.Path(first.cache_path)
                if boundary == 'migration':
                    Cache(paths.cache_dir).clear()
                    active = paths.cache_dir / 'current.jpg'
                    active.write_bytes(self.image)
                image = support.make_image((640, 360), color='blue')
                fetcher.responses[MANIFEST_URL] = Response(
                    url=MANIFEST_URL, status=200,
                    data=support.manifest_for(image, image_url=IMAGE_URL, width=640, height=360))
                fetcher.responses[IMAGE_URL] = Response(url=IMAGE_URL, status=200, data=image)
                orphan = paths.cache_dir / 'secondary-cleanup.jpg'
                triggered = []
                primary = 'injected %s filesystem error' % boundary

                def replace(src, dst):
                    dst = pathlib.Path(dst)
                    if ((boundary == 'staging' and dst.name.startswith('.incoming-'))
                            or (boundary == 'index' and dst.name == Cache.INDEX)):
                        triggered.append(True)
                        orphan.write_bytes(b'orphan')
                        raise OSError(primary)
                    return real_replace(src, dst)

                def link(src, dst):
                    if boundary in ('link', 'migration'):
                        triggered.append(True)
                        orphan.write_bytes(b'orphan')
                        raise OSError(primary)
                    return real_link(src, dst)

                def unlink(path, **kwargs):
                    if triggered and (path == orphan or path.name.startswith('.incoming-')
                                      or '.tmp-' in path.name):
                        raise OSError('secondary cleanup error')
                    return real_unlink(path, **kwargs)

                if boundary == 'apply':
                    platform.fail_with = OSError(primary)
                with patch('os.replace', side_effect=replace), patch('os.link', side_effect=link), \
                        patch.object(pathlib.Path, 'unlink', unlink):
                    try:
                        result, _ = self.run_once(config, paths, fetcher, platform=platform, force=True)
                    except OSError as error:
                        self.fail('expected a recorded failure, not raw OSError: %s' % error)
                self.assertEqual(result.exit_code, 1)
                self.assertIn(primary, result.detail)
                state = State(paths.state_file).load()
                self.assertFalse(state.last_attempt['ok'])
                self.assertIn(primary, state.last_attempt['reason'])
                self.assertEqual(state.last_success['cache_path'], first.cache_path)
                self.assertEqual(active.read_bytes(), self.image)

    def test_desktop_failure_is_reported_and_not_recorded_as_success(self):
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        platform.fail_with = PlatformError('xfconf-query failed: no session')
        result, platform = self.run_once(config, paths, fetcher, platform=platform)
        self.assertEqual(result.outcome, 'failed')
        self.assertFalse(result.applied)
        state = State(paths.state_file).load()
        self.assertIsNone(state.last_success)
        self.assertIn('xfconf-query', state.last_attempt['reason'])

    def test_unconfirmed_desktop_state_is_a_failure(self):
        config, paths, fetcher = self.build()
        platform = support.FakePlatform(reports='/some/other/wallpaper.png',
                                        apply_effective=False)
        result, _ = self.run_once(config, paths, fetcher, platform=platform)
        self.assertEqual(result.outcome, 'failed')
        self.assertIn('could not be confirmed', result.detail)

    # --- resolution floor ---------------------------------------------------
    def test_minimum_resolution_from_the_config_is_enforced(self):
        config, paths, fetcher = self.build()
        config['source']['min_width'] = 1920
        config['source']['min_height'] = 1080
        result, platform = self.run_once(config, paths, fetcher)
        self.assertEqual(result.outcome, 'failed')
        self.assertIn('minimum', result.detail)
        self.assertEqual(platform.applied, [])

    # --- cheap check mode ---------------------------------------------------
    def test_ordinary_same_day_desktop_drift_is_repaired_offline_with_bounded_confirmation(self):
        from unittest.mock import patch
        for outcome in ('delayed-confirmation', 'rejected', 'never-confirmed'):
            with self.subTest(outcome=outcome):
                self.root = pathlib.Path(self.directory.name) / outcome
                self.root.mkdir()
                config, paths, fetcher = self.build()
                first, _ = self.run_once(config, paths, fetcher)
                before = State(paths.state_file).load().last_success
                fetcher.requests.clear()
                if outcome == 'delayed-confirmation':
                    platform = support.SlowReportingPlatform(lag=2)
                else:
                    platform = support.FakePlatform(apply_effective=outcome != 'never-confirmed')
                platform.reports = '/user-changed-wallpaper.jpg'
                if outcome == 'rejected':
                    platform.fail_with = PlatformError('offline repair rejected')
                waited = []
                with patch('paperdrop.pipeline.resolve_source',
                           side_effect=AssertionError('same-day repair must not resolve/fetch')), \
                        patch.object(platform, 'current', wraps=platform.current) as current:
                    result, _ = self.run_once(config, paths, fetcher, platform=platform,
                                              sleep=waited.append)
                self.assertEqual(fetcher.requests, [])
                self.assertEqual(platform.apply_calls, 1)
                self.assertLessEqual(current.call_count, 1 + config['apply']['confirm_attempts'])
                self.assertLessEqual(len(waited), config['apply']['confirm_attempts'] - 1)
                state = State(paths.state_file).load()
                if outcome == 'delayed-confirmation':
                    self.assertEqual(result.outcome, 'reapplied')
                    self.assertEqual(state.last_success['cache_path'], first.cache_path)
                    self.assertEqual(state.last_success['checks'], ['reapplied'])
                    self.assertTrue(state.last_attempt['ok'])
                    self.assertEqual(len(waited), 2)
                else:
                    self.assertEqual(result.exit_code, 1)
                    self.assertEqual(state.last_success, before)
                    self.assertFalse(state.last_attempt['ok'])
                self.assertEqual(Cache(paths.cache_dir).current().read_bytes(), self.image)

    def test_check_mode_repairs_the_desktop_from_the_cache_without_downloading(self):
        config, paths, fetcher = self.build()
        self.run_once(config, paths, fetcher)
        cached = Cache(paths.cache_dir).current()
        platform = support.FakePlatform(reports='/some/other/wallpaper.png')
        fetcher.requests.clear()
        result, platform = self.run_once(config, paths, fetcher, platform=platform,
                                         check_only=True)
        self.assertEqual(result.outcome, 'reapplied')
        self.assertEqual(platform.applied, [str(cached)])
        self.assertEqual(fetcher.requests, [], 'check mode must not touch the network')

    def test_a_second_screen_showing_an_older_wallpaper_is_repaired_without_downloading(self):
        """The reported bug: the main screen is up to date, an external one is not.

        The desktop state used to be read from the main screen alone, so the same-day
        check answered "already up to date" while a second display kept the wallpaper
        it had before (macOS restores a display's own image when it is reconnected).
        """
        config, paths, fetcher = self.build()
        self.run_once(config, paths, fetcher)
        cached = Cache(paths.cache_dir).current()
        platform = support.FakePlatform(
            reports=str(cached),
            screens=[('screen 0 Built-in Retina Display', str(cached)),
                     ('screen 1 LG UltraFine', '/old/current.jpg')])
        fetcher.requests.clear()
        result, platform = self.run_once(config, paths, fetcher, platform=platform)
        self.assertEqual(result.outcome, 'reapplied')
        self.assertEqual(platform.applied, [str(cached)])
        self.assertEqual(fetcher.requests, [], 'repairing a screen must not touch the network')

    def test_the_repair_report_names_the_screen_that_does_not_match(self):
        config, paths, fetcher = self.build()
        self.run_once(config, paths, fetcher)
        cached = Cache(paths.cache_dir).current()
        platform = support.FakePlatform(
            reports=str(cached),
            screens=[('screen 0 Built-in Retina Display', str(cached)),
                     ('screen 1 LG UltraFine', '/old/current.jpg')])
        messages = []
        logger = logging.getLogger('paperdrop-repair-detail')
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(_Collect(messages))
        self.run_once(config, paths, fetcher, platform=platform, logger=logger)
        joined = '\n'.join(messages)
        self.assertIn('LG UltraFine', joined)
        self.assertNotIn('Built-in Retina Display', joined,
                         'the screen that is already correct must not be reported as wrong')

    def test_a_screen_that_appears_while_watching_is_repaired(self):
        """Reconnecting a display is the case the native event never delivered.

        macOS restores that display's own wallpaper; the poll sees a new entry that
        does not show the cached image and re-applies it once.
        """
        config, paths, fetcher = self.build()
        self.run_once(config, paths, fetcher)
        cached = Cache(paths.cache_dir).current()
        platform = support.FakePlatform(
            reports=str(cached),
            screens=[('screen 0 Built-in Retina Display', str(cached))])
        slept = []

        def sleeper(seconds):
            slept.append(seconds)
            if len(slept) == 1:
                # the external display comes back with the image it remembered
                platform.screens.append(('screen 1 LG UltraFine', '/old/current.jpg'))

        self.assertEqual(screen_poll_loop(config, paths, platform, logger=self.logger,
                                          sleeper=sleeper, iterations=3), 0)
        self.assertEqual(platform.applied, [str(cached)])
        self.assertEqual(slept, [30, 30, 30], 'the poll interval comes from the config')

    def test_a_screen_that_stays_wrong_is_not_retried_forever(self):
        """A display that keeps its own wallpaper must not cause an endless loop."""
        config, paths, fetcher = self.build()
        self.run_once(config, paths, fetcher)
        cached = Cache(paths.cache_dir).current()

        class StubbornScreen(support.FakePlatform):
            def apply(self, image_path):
                result = super().apply(image_path)
                target, _ = self.screens[-1]
                self.screens[-1] = (target, '/old/current.jpg')
                return result

        platform = StubbornScreen(reports=str(cached),
                                  screens=[('screen 0 Built-in Retina Display', str(cached)),
                                           ('screen 1 LG UltraFine', '/old/current.jpg')])
        screen_poll_loop(config, paths, platform, logger=self.logger,
                         sleeper=lambda seconds: None, iterations=8)
        self.assertEqual(platform.apply_calls, 3)

    def test_screen_polling_is_off_when_the_interval_is_zero(self):
        """The switch has to be read, not merely accepted by the config loader."""
        config, paths, fetcher = self.build()
        config['schedule']['screen_poll_seconds'] = 0
        platform = support.FakePlatform(reports='/old/current.jpg',
                                        screens=[('screen 0 Built-in Display', '/old/current.jpg')])
        slept = []
        self.assertEqual(screen_poll_loop(config, paths, platform, logger=self.logger,
                                          sleeper=slept.append, iterations=3), 0)
        self.assertEqual(slept, [])
        self.assertEqual(platform.apply_calls, 0)

    def test_check_mode_is_silent_when_everything_is_fine(self):
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        self.run_once(config, paths, fetcher, platform=platform)
        fetcher.requests.clear()
        result, platform = self.run_once(config, paths, fetcher, platform=platform, check_only=True)
        self.assertEqual(result.outcome, 'unchanged')
        self.assertEqual(fetcher.requests, [])
        self.assertEqual(platform.apply_calls, 1)

    def test_check_mode_with_a_changed_source_still_does_not_download(self):
        """--check is the cheap repair path: a source change is reported, not fetched."""
        config, paths, fetcher = self.build()
        platform = support.FakePlatform()
        self.run_once(config, paths, fetcher, platform=platform)
        fetcher.requests.clear()
        config['source']['url'] = 'https://example.test/other.json'
        result, _ = self.run_once(config, paths, fetcher, platform=platform, check_only=True)
        self.assertEqual(fetcher.requests, [], 'check mode must stay offline')
        self.assertEqual(result.outcome, 'unchanged')

    def test_check_rejects_missing_or_symlink_current_without_deleting_recoverable_data(self):
        for kind in ('missing', 'symlink'):
            with self.subTest(kind=kind):
                self.root = pathlib.Path(self.directory.name) / kind
                self.root.mkdir()
                config, paths, fetcher = self.build()
                cache = Cache(paths.cache_dir)
                recoverable = cache.publish(self.image, '.jpg')
                bad = cache.directory / ('image-' + '0' * 64 + '.jpg')
                if kind == 'symlink':
                    bad.symlink_to(recoverable)
                (cache.directory / Cache.INDEX).write_text(json.dumps(
                    {'version': 1, 'current': bad.name, 'previous': None}))
                before = set(cache.directory.iterdir())
                result, platform = self.run_once(config, paths, fetcher, check_only=True)
                self.assertEqual(result.exit_code, 1, 'must not report unchanged for an invalid URL')
                self.assertEqual(set(cache.directory.iterdir()), before)
                self.assertEqual(recoverable.read_bytes(), self.image)
                self.assertEqual(fetcher.requests, [])
                self.assertEqual(platform.applied, [])
                state = State(paths.state_file).load()
                self.assertIsNone(state.last_success)
                self.assertFalse(state.last_attempt['ok'])

    def test_watcher_flock_and_cli_update_cannot_restore_a_stale_resolved_url(self):
        import argparse
        import fcntl
        import threading
        from unittest.mock import patch
        from paperdrop.cli import _run
        config, paths, fetcher = self.build()
        platform = PathCachingDesktop()
        first, _ = self.run_once(config, paths, fetcher, platform=platform)
        active = pathlib.Path(first.cache_path)
        args = argparse.Namespace(force=True, check=False, json=False)
        errors = []

        def callback(resolved, proceed, contended):
            # Deterministic POSIX model of the Swift event's resolve+apply lock.
            try:
                with open(paths.lock_file, 'a+') as handle:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        contended.set()
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    path = Cache(paths.cache_dir)._roles()[Cache.ACTIVE]
                    resolved.set()
                    if not proceed.wait(5):
                        raise AssertionError('callback release timed out')
                    platform.apply(path)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except BaseException as error:
                errors.append(error)

        resolved, proceed, contended = (threading.Event() for _ in range(3))
        worker = threading.Thread(target=callback, args=(resolved, proceed, contended))
        worker.start()
        self.addCleanup(lambda: (proceed.set(), worker.join(5)))
        self.assertTrue(resolved.wait(5))
        image = support.make_image((640, 360), color='blue')
        fetcher.responses[MANIFEST_URL] = Response(
            url=MANIFEST_URL, status=200,
            data=support.manifest_for(image, image_url=IMAGE_URL, width=640, height=360))
        fetcher.responses[IMAGE_URL] = Response(url=IMAGE_URL, status=200, data=image)
        with patch('paperdrop.cli._platform', return_value=platform), \
                patch('paperdrop.cli.LOG', self.logger), \
                patch('paperdrop.pipeline.Fetcher.from_config', return_value=fetcher), \
                patch('paperdrop.cli._print'):
            before = len(fetcher.requests)
            self.assertEqual(_run(args, config, paths), 0)  # busy, not a publication
            self.assertEqual(len(fetcher.requests), before)
            self.assertEqual(Cache(paths.cache_dir).current(), active)
            proceed.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())

            entered, release = threading.Event(), threading.Event()
            original_apply = platform.apply
            def paused_apply(path):
                if pathlib.Path(path) != active:
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('updater release timed out')
                return original_apply(path)
            update_result = []
            def update():
                try:
                    update_result.append(_run(args, config, paths))
                except BaseException as error:
                    errors.append(error)
            with patch.object(platform, 'apply', side_effect=paused_apply):
                updater = threading.Thread(target=update)
                updater.start()
                self.addCleanup(lambda: (release.set(), updater.join(5)))
                self.assertTrue(entered.wait(5))
                resolved2, proceed2, contended2 = (threading.Event() for _ in range(3))
                proceed2.set()
                waiter = threading.Thread(target=callback, args=(resolved2, proceed2, contended2))
                waiter.start()
                self.addCleanup(lambda: (release.set(), waiter.join(5)))
                self.assertTrue(contended2.wait(5))
                self.assertFalse(resolved2.is_set(), 'must resolve only after the update finishes')
                release.set()
                updater.join(5)
                waiter.join(5)
                self.assertFalse(updater.is_alive() or waiter.is_alive())
            self.assertEqual(update_result, [0])
        self.assertEqual(errors, [])
        self.assertTrue(resolved2.is_set())
        self.assertEqual(platform.visible, image)
        self.assertEqual(platform.current(), str(Cache(paths.cache_dir).current()))
        self.assertEqual(active.read_bytes(), self.image)

    def test_offline_apply_does_not_migrate_or_prune_during_an_update(self):
        from paperdrop.errors import BusyError
        from paperdrop.lock import FileLock
        config, paths, fetcher = self.build()
        cache = Cache(paths.cache_dir)
        cache.ensure()
        legacy = cache.directory / 'current.jpg'
        legacy.write_bytes(self.image)
        platform = support.FakePlatform()
        with FileLock(paths.lock_file):
            with self.assertRaises(BusyError):
                apply_cached(config, paths, platform, logger=self.logger)
            self.assertTrue(legacy.exists())
            self.assertFalse((cache.directory / Cache.INDEX).exists())
            self.assertEqual(platform.applied, [])
        self.assertEqual(apply_cached(config, paths, platform, logger=self.logger), cache.current())
        # The offline apply releases the lock; a resident watch never owns it.
        with FileLock(paths.lock_file):
            pass

    def test_apply_cached_works_without_any_network_access(self):
        config, paths, fetcher = self.build()
        self.run_once(config, paths, fetcher)
        platform = support.FakePlatform()
        applied = apply_cached(config, paths, platform, logger=self.logger)
        self.assertEqual(platform.applied, [str(applied)])

    def test_apply_cached_without_a_cache_is_a_clear_error(self):
        config, paths, fetcher = self.build()
        with self.assertRaises(Exception) as caught:
            apply_cached(config, paths, support.FakePlatform(), logger=self.logger)
        self.assertIn('run', str(caught.exception))


class ImageModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = pathlib.Path(self.directory.name)
        self.image = support.make_image((800, 400))
        self.url = 'https://example.test/latest.jpg'
        self.logger = support.silent_logger()
        config, config_path = support.write_config(self.root, self.url, mode='image')
        self.config = config
        self.paths = Paths.from_config(config, config_file=config_path)

    def fetcher(self, **kwargs):
        options = {'responses': {self.url: Response(url=self.url, status=200, data=self.image,
                                                    headers={'Content-Type': 'image/jpeg',
                                                             'ETag': '"v1"'})}}
        options.update(kwargs)
        return support.FakeFetcher(**options)

    def test_image_mode_downloads_and_records_a_weak_guarantee_warning(self):
        fetcher = self.fetcher()
        result = run_once(self.config, self.paths, support.FakePlatform(), fetcher=fetcher,
                          now=NOW, logger=self.logger)
        self.assertEqual(result.outcome, 'updated')
        self.assertTrue(result.warnings)
        self.assertTrue(any('no consistency guarantee' in warning for warning in result.warnings))
        state = State(self.paths.state_file).load()
        self.assertEqual(state.last_success['etag'], '"v1"')

    def test_an_unchanged_image_is_detected_with_a_conditional_request(self):
        fetcher = self.fetcher()
        run_once(self.config, self.paths, support.FakePlatform(), fetcher=fetcher, now=NOW,
                 logger=self.logger)
        second = self.fetcher(conditional={self.url: Response(url=self.url, status=304)})
        result = run_once(self.config, self.paths, support.FakePlatform(), fetcher=second,
                          now=NOW, force=True, logger=self.logger)
        self.assertFalse(result.downloaded)
        self.assertIn(('CONDITIONAL', self.url), second.requests)

    def test_image_mode_identity_is_not_claimed_to_be_verified(self):
        fetcher = self.fetcher()
        result = run_once(self.config, self.paths, support.FakePlatform(), fetcher=fetcher,
                          now=NOW, logger=self.logger)
        self.assertTrue(result.identity.startswith('unverified:'))
        self.assertTrue(str(result.cache_path))


if __name__ == '__main__':
    unittest.main(verbosity=2)
