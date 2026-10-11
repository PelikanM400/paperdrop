"""Verification of bytes: length, SHA-256, real decode, dimensions."""
from __future__ import annotations

import unittest

import support

from paperdrop.errors import VerificationError
from paperdrop.util import sha256_hex
from paperdrop.verify import check_bytes, check_image, extension_for


class BytesTests(unittest.TestCase):
    def test_empty_body_is_rejected(self):
        with self.assertRaises(VerificationError):
            check_bytes(b'')

    def test_size_mismatch_is_rejected(self):
        data = support.make_image()
        with self.assertRaises(VerificationError) as caught:
            check_bytes(data, expected_size=len(data) + 1)
        self.assertIn('Size mismatch', str(caught.exception))

    def test_sha_mismatch_is_rejected(self):
        with self.assertRaises(VerificationError) as caught:
            check_bytes(support.make_image(), expected_sha256='a' * 64)
        self.assertIn('SHA-256 mismatch', str(caught.exception))

    def test_malformed_expected_sha_is_rejected(self):
        with self.assertRaises(VerificationError) as caught:
            check_bytes(support.make_image(), expected_sha256='NOT-HEX')
        self.assertIn('malformed', str(caught.exception))

    def test_matching_sha_is_returned(self):
        data = support.make_image()
        self.assertEqual(check_bytes(data, expected_sha256=sha256_hex(data)), sha256_hex(data))


class ImageTests(unittest.TestCase):
    def test_real_image_reports_its_geometry(self):
        width, height, image_format = check_image(support.make_image((60, 40)))
        self.assertEqual((width, height), (60, 40))
        self.assertEqual(image_format, 'JPEG')

    def test_truncated_image_is_rejected_even_though_the_header_looks_fine(self):
        data = support.make_image((800, 600))
        truncated = data[:len(data) // 3]
        with self.assertRaises(VerificationError):
            check_image(truncated)

    def test_non_image_bytes_are_rejected(self):
        with self.assertRaises(VerificationError) as caught:
            check_image(b'<html>not an image</html>' * 40)
        self.assertIn('decodable', str(caught.exception))

    def test_dimension_mismatch_against_the_manifest_is_rejected(self):
        with self.assertRaises(VerificationError) as caught:
            check_image(support.make_image((60, 40)), expected_width=100, expected_height=200)
        self.assertIn('Dimension mismatch', str(caught.exception))

    def test_minimum_resolution_is_enforced(self):
        with self.assertRaises(VerificationError) as caught:
            check_image(support.make_image((60, 40)), min_width=1920, min_height=1080)
        self.assertIn('below the configured minimum', str(caught.exception))

    def test_pixel_budget_is_enforced_before_decoding(self):
        with self.assertRaises(VerificationError) as caught:
            check_image(support.make_image((60, 40)), max_pixels=100)
        self.assertIn('pixel limit', str(caught.exception))

    def test_png_and_webp_are_accepted_and_mapped_to_extensions(self):
        for image_format, expected in (('PNG', '.png'), ('WEBP', '.webp')):
            data = support.make_image((30, 20), fmt=image_format)
            width, height, detected = check_image(data)
            self.assertEqual(detected, image_format)
            self.assertEqual(extension_for(detected), expected)

    def test_extension_falls_back_to_content_type_then_url(self):
        self.assertEqual(extension_for(None, content_type='image/png'), '.png')
        self.assertEqual(extension_for(None, url='https://example.test/a.JPEG'), '.jpg')
        self.assertEqual(extension_for(None), '.img')


if __name__ == '__main__':
    unittest.main(verbosity=2)
