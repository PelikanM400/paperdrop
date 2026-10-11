"""Verification of downloaded bytes and of decodability.

A download is only accepted when every check the source gives us passes:
declared size, SHA-256, real decode, and the configured resolution floor.
"""
from __future__ import annotations

import io
import warnings

from PIL import Image

from .errors import VerificationError
from .util import is_hex64, sha256_hex

IMAGE_FORMAT_EXTENSION = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif',
                          'BMP': '.bmp', 'TIFF': '.tif'}


def check_bytes(data, expected_sha256=None, expected_size=None):
    """Validate length and (when the source provides it) the SHA-256."""
    if not data:
        raise VerificationError('Downloaded body is empty.')
    if expected_size is not None and len(data) != expected_size:
        raise VerificationError('Size mismatch: got %d bytes, source declared %d.'
                                % (len(data), expected_size))
    digest = sha256_hex(data)
    if expected_sha256 is not None:
        if not is_hex64(expected_sha256):
            raise VerificationError('Source gave a malformed sha256: %r' % (expected_sha256,))
        if digest != expected_sha256:
            raise VerificationError('SHA-256 mismatch: got %s, source declared %s.'
                                    % (digest, expected_sha256))
    return digest


def check_image(data, expected_width=None, expected_height=None, min_width=0, min_height=0,
                max_pixels=100000000):
    """Fully decode the image and check dimensions. Returns (width, height, format)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                width, height = probe.size
    except Image.DecompressionBombError as error:  # pragma: no cover - extreme input
        raise VerificationError('Image rejected as a decompression bomb: %s' % error) from None
    except Exception as error:
        raise VerificationError('Not a decodable image: %s' % error) from None
    if width <= 0 or height <= 0:
        raise VerificationError('Image reports an empty size.')
    if width * height > max_pixels:
        raise VerificationError('Image is %dx%d (%d pixels), above the %d pixel limit.'
                                % (width, height, width * height, max_pixels))
    if expected_width is not None and expected_height is not None:
        if (width, height) != (expected_width, expected_height):
            raise VerificationError('Dimension mismatch: got %dx%d, source declared %dx%d.'
                                    % (width, height, expected_width, expected_height))
    if width < min_width or height < min_height:
        raise VerificationError('Image is %dx%d, below the configured minimum %dx%d.'
                                % (width, height, min_width, min_height))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image.load()  # proves the pixels are really there, not just the header
                image_format = image.format
    except Exception as error:
        raise VerificationError('Image failed to decode: %s' % error) from None
    return width, height, image_format


def extension_for(image_format, url='', content_type=''):
    if image_format in IMAGE_FORMAT_EXTENSION:
        return IMAGE_FORMAT_EXTENSION[image_format]
    lowered = (content_type or '').lower()
    for kind, extension in (('jpeg', '.jpg'), ('png', '.png'), ('webp', '.webp')):
        if kind in lowered:
            return extension
    lowered_url = (url or '').lower().split('?')[0]
    for extension in ('.jpg', '.jpeg', '.png', '.webp'):
        if lowered_url.endswith(extension):
            return '.jpg' if extension == '.jpeg' else extension
    return '.img'
