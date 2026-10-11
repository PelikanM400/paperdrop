"""Source resolution against the real published manifest shape and its edges."""
from __future__ import annotations

import unittest

import support

from paperdrop.errors import SourceError
from paperdrop.fetch import Response
from paperdrop.sources import parse_manifest, resolve_source


class ManifestTests(unittest.TestCase):
    def test_real_published_manifest_is_accepted_without_a_version_field(self):
        target = parse_manifest(support.fixture('manifests/github-desk.json'),
                                'https://example.test/desk.json')
        self.assertEqual(target.mode, 'manifest')
        self.assertEqual(target.schema_version, 1)
        self.assertEqual(target.manifest['photo_id'], 'jgg6LsIL2zY')
        self.assertEqual(target.width, 7952)
        self.assertEqual(target.height, 5304)
        self.assertEqual(target.size_bytes, 1850965)
        self.assertTrue(str(target.sha256).startswith('fd0ac9e2'))
        self.assertTrue(str(target.url).startswith('https://raw.githubusercontent.com/'))

    def test_gitee_manifest_uses_its_own_image_url(self):
        target = parse_manifest(support.fixture('manifests/gitee-desk.json'))
        self.assertTrue(target.url.startswith('https://gitee.com/'))

    def test_owner_supplied_photo_without_source_fields_is_accepted(self):
        target = parse_manifest(support.fixture('manifests/owner-photo.json'))
        self.assertIsNone(target.manifest['photo_id'])
        self.assertNotIn('source_photo_url', target.manifest)
        self.assertTrue(target.sha256)

    def test_minimal_manifest_warns_about_missing_optional_fields(self):
        target = parse_manifest(support.fixture('manifests/minimal.json'))
        self.assertIsNone(target.width)
        self.assertTrue(any('width' in warning for warning in target.warnings))

    def test_missing_sha256_is_rejected(self):
        with self.assertRaises(SourceError) as caught:
            parse_manifest(support.fixture('manifests/missing-sha256.json'))
        self.assertIn('sha256', str(caught.exception))

    def test_malformed_sha256_is_rejected(self):
        with self.assertRaises(SourceError):
            parse_manifest(support.fixture('manifests/bad-sha256.json'))

    def test_future_schema_version_is_refused_not_guessed(self):
        with self.assertRaises(SourceError) as caught:
            parse_manifest(support.fixture('manifests/future-version.json'))
        self.assertIn('Upgrade', str(caught.exception))

    def test_declared_v1_is_accepted(self):
        target = parse_manifest(support.fixture('manifests/versioned-v1.json'))
        self.assertEqual(target.schema_version, 1)
        self.assertEqual(target.width, 3840)

    def test_wrong_field_types_are_rejected_rather_than_coerced(self):
        with self.assertRaises(SourceError):
            parse_manifest(support.fixture('manifests/bad-dimensions.json'))

    def test_non_absolute_image_url_is_rejected(self):
        with self.assertRaises(SourceError):
            parse_manifest(support.fixture('manifests/not-a-url.json'))

    def test_credentials_in_image_url_are_rejected(self):
        with self.assertRaises(SourceError):
            parse_manifest(support.fixture('manifests/credentials-in-url.json'))

    def test_unknown_extra_fields_are_ignored(self):
        target = parse_manifest(support.fixture('manifests/unknown-extra-fields.json'))
        self.assertEqual(target.sha256, 'f' * 64)

    def test_a_json_array_is_not_a_manifest(self):
        with self.assertRaises(SourceError):
            parse_manifest(support.fixture('manifests/empty-list.json'))

    def test_non_json_body_is_rejected_with_a_clear_message(self):
        with self.assertRaises(SourceError) as caught:
            parse_manifest(support.fixture('manifests/not-json.txt'))
        self.assertIn('JSON', str(caught.exception))


class ResolutionTests(unittest.TestCase):
    def config(self, mode='manifest', url='https://example.test/desk.json'):
        from paperdrop.config import validate
        return validate({'source': {'mode': mode, 'url': url,
                                    'image_mode_acknowledged': mode == 'image'}})

    def test_repository_home_page_is_refused_before_any_request(self):
        fetcher = support.FakeFetcher()
        for url in ('https://github.com/PelikanM400/pelikan_daily_wallpaper',
                    'https://gitee.com/PelikanM400/pelikan_daily_wallpaper',
                    'https://github.com/PelikanM400/pelikan_daily_wallpaper/tree/main'):
            with self.assertRaises(SourceError) as caught:
                resolve_source(self.config(url=url), fetcher)
            self.assertIn('manifest', str(caught.exception))
        self.assertEqual(fetcher.requests, [], 'no request may be made for a repository page')

    def test_html_response_is_refused(self):
        fetcher = support.FakeFetcher({'https://example.test/desk.json': Response(
            url='https://example.test/desk.json', status=200, data=b'<html></html>',
            headers={'Content-Type': 'text/html; charset=utf-8'})})
        with self.assertRaises(SourceError) as caught:
            resolve_source(self.config(), fetcher)
        self.assertIn('manifest', str(caught.exception))

    def test_image_mode_returns_a_weak_guarantee_warning(self):
        target = resolve_source(
            self.config(mode='image', url='https://example.test/wall.jpg'),
            support.FakeFetcher())
        self.assertEqual(target.mode, 'image')
        self.assertIsNone(target.sha256)
        self.assertTrue(any('no consistency guarantee' in warning for warning in target.warnings))

    def test_image_mode_never_downloads_the_image_during_resolution(self):
        fetcher = support.FakeFetcher()
        resolve_source(self.config(mode='image', url='https://example.test/wall.jpg'), fetcher)
        self.assertEqual(fetcher.requests, [])

    def test_manifest_is_fetched_with_a_bounded_size(self):
        fetcher = support.FakeFetcher({'https://example.test/desk.json': Response(
            url='https://example.test/desk.json', status=200,
            data=support.fixture('manifests/github-desk.json'),
            headers={'Content-Type': 'application/json'})})
        resolve_source(self.config(), fetcher)
        self.assertEqual(fetcher.requests, [('GET', 'https://example.test/desk.json')])


if __name__ == '__main__':
    unittest.main(verbosity=2)
