"""Configuration: strict validation and the manifest-mode default."""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import support  # noqa: F401

from paperdrop.config import ConfigError, example_config, load_config, validate


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / 'config.json'

    def write(self, payload):
        self.path.write_text(json.dumps(payload), encoding='utf-8')
        return self.path

    def test_example_config_is_itself_valid(self):
        config = validate(example_config())
        self.assertEqual(config['source']['mode'], 'manifest')

    def test_missing_file_reports_the_expected_path(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn(str(self.path), str(caught.exception))

    def test_unknown_key_is_rejected_with_the_allowed_names(self):
        self.write({'source': {'url': 'https://example.test/m.json', 'mdoe': 'manifest'}})
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn('mdoe', str(caught.exception))
        self.assertIn('mode', str(caught.exception))

    def test_comment_keys_are_ignored(self):
        self.write({'_note': 'a comment', 'source': {'_why': 'because',
                                                     'url': 'https://example.test/m.json'}})
        config = load_config(self.path)[0]
        self.assertEqual(config['source']['mode'], 'manifest')

    def test_url_is_required(self):
        self.write({'source': {'mode': 'manifest'}})
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn('source.url', str(caught.exception))

    def test_plain_http_requires_an_explicit_opt_in(self):
        self.write({'source': {'url': 'http://example.test/m.json'}})
        with self.assertRaises(ConfigError):
            load_config(self.path)
        self.write({'source': {'url': 'http://example.test/m.json'},
                    'http': {'allow_http': True}})
        self.assertTrue(load_config(self.path)[0]['http']['allow_http'])

    def test_credentials_in_the_source_url_are_rejected(self):
        self.write({'source': {'url': 'https://user:pass@example.test/m.json'}})
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn('credentials', str(caught.exception))

    def test_image_mode_requires_acknowledgement(self):
        self.write({'source': {'mode': 'image', 'url': 'https://example.test/a.jpg'}})
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn('image_mode_acknowledged', str(caught.exception))
        self.write({'source': {'mode': 'image', 'url': 'https://example.test/a.jpg',
                               'image_mode_acknowledged': True}})
        self.assertEqual(load_config(self.path)[0]['source']['mode'], 'image')

    def test_unknown_mode_is_rejected(self):
        self.write({'source': {'mode': 'gallery', 'url': 'https://example.test/m.json'}})
        with self.assertRaises(ConfigError) as caught:
            load_config(self.path)
        self.assertIn('manifest', str(caught.exception))

    def test_daily_time_format_is_checked(self):
        for bad in ('5:05', '25:00', '05:70', 'noon'):
            self.write({'source': {'url': 'https://example.test/m.json'},
                        'schedule': {'daily_time': bad}})
            with self.assertRaises(ConfigError):
                load_config(self.path)
        self.write({'source': {'url': 'https://example.test/m.json'},
                    'schedule': {'daily_time': '04:30'}})
        self.assertEqual(load_config(self.path)[0]['schedule']['daily_time'], '04:30')

    def test_numbers_must_be_positive_integers(self):
        for key in ('retries', 'timeout_seconds', 'max_bytes'):
            self.write({'source': {'url': 'https://example.test/m.json'},
                        'http': {key: 0}})
            with self.assertRaises(ConfigError):
                load_config(self.path)

    def test_defaults_are_applied_for_omitted_sections(self):
        self.write({'source': {'url': 'https://example.test/m.json'}})
        config = load_config(self.path)[0]
        self.assertEqual(config['apply']['scaling'], 'fill')
        self.assertEqual(config['apply']['screens'], 'all')
        self.assertIsNone(config['apply']['xfce_image_style'])
        self.assertTrue(config['schedule']['catch_up_on_login'])

    def test_unsupported_scaling_is_rejected(self):
        self.write({'source': {'url': 'https://example.test/m.json'},
                    'apply': {'scaling': 'stretch'}})
        with self.assertRaises(ConfigError):
            load_config(self.path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
