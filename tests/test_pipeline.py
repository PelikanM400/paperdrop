"""End-to-end runs with a fake transport: caching, no-op runs, failure handling."""
from __future__ import annotations

import datetime
import json
import pathlib
import tempfile
import unittest

import support

from paperdrop.cache import Cache
from paperdrop.errors import FetchError, PlatformError
from paperdrop.fetch import Response
from paperdrop.paths import Paths
from paperdrop.pipeline import apply_cached, run_once
from paperdrop.state import State
from paperdrop.util import sha256_hex

MANIFEST_URL = 'https://example.test/desk.json'
IMAGE_URL = 'https://example.test/commit/desk.jpg'
NOW = datetime.datetime(2030, 1, 2, 4, 30, tzinfo=datetime.timezone.utc)


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
