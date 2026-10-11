"""Source resolution: manifest mode and image mode.

Manifest mode is the recommended one because the manifest carries a SHA-256:
that is what lets PaperDrop skip a download when nothing changed, and what ties
the manifest to the exact image bytes.

Image mode is supported but explicitly weaker; see docs/sources.md.
"""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

from .errors import SourceError
from .fetch import MANIFEST_MAX_BYTES
from .util import is_hex64, looks_like_repository_page

SCHEMA_NAME = 'paperdrop-manifest'
SUPPORTED_SCHEMA_VERSION = 1

REPO_PAGE_HINT = ('That looks like a repository or directory page, not a wallpaper source. '
                  'Point source.url at a JSON manifest or at a direct image URL.')


@dataclass
class Target:
    mode: str
    source_url: str
    url: str
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    date: Optional[str] = None
    published_at: Optional[str] = None
    schema_version: int = SUPPORTED_SCHEMA_VERSION
    manifest: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def identity(self):
        """What makes this image "the same" as a previously published one."""
        if self.sha256:
            return self.sha256
        return 'unverified:' + self.url

    def describe(self):
        bits = []
        if self.date:
            bits.append(self.date)
        if self.width and self.height:
            bits.append('%dx%d' % (self.width, self.height))
        if self.sha256:
            bits.append('sha256=' + self.sha256[:12])
        return ' '.join(bits) or self.url


def _reject_repository_page(url):
    if looks_like_repository_page(url):
        raise SourceError(REPO_PAGE_HINT)


def _require_int(manifest, key, minimum=1):
    value = manifest.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SourceError('Manifest field "%s" must be an integer >= %d.' % (key, minimum))
    return value


def _require_str(manifest, key):
    value = manifest.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SourceError('Manifest field "%s" must be a non-empty string.' % key)
    return value


def parse_manifest(payload, source_url=''):
    """Validate a manifest document and return a Target. Implements the spec's C1-C3."""
    try:
        manifest = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as error:
        raise SourceError('Source is not valid JSON: %s' % error) from None
    if not isinstance(manifest, dict):
        raise SourceError('Manifest must be a JSON object.')

    warnings = []
    scheme = manifest.get('schema')
    version = manifest.get('schema_version')
    if version is None:
        # Backwards compatible with publishers that predate the version field.
        schema_version = SUPPORTED_SCHEMA_VERSION
        if isinstance(scheme, str) and scheme and scheme != SCHEMA_NAME:
            warnings.append('Unknown manifest schema "%s"; assuming version 1.' % scheme)
    else:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise SourceError('Manifest field "schema_version" must be a positive integer.')
        schema_version = version
        if schema_version > SUPPORTED_SCHEMA_VERSION:
            raise SourceError(
                'Manifest declares schema_version %d, but this PaperDrop supports up to %d. '
                'Upgrade PaperDrop.' % (schema_version, SUPPORTED_SCHEMA_VERSION))

    image_url = manifest.get('image_url')
    if not isinstance(image_url, str) or not image_url.strip():
        raise SourceError('Manifest is missing the required field "image_url".')
    image_url = image_url.strip()
    parts = urllib.parse.urlsplit(image_url)
    if parts.scheme not in ('http', 'https'):
        raise SourceError('Manifest "image_url" must be an absolute http(s) URL.')
    if parts.username or parts.password:
        raise SourceError('Manifest "image_url" must not contain credentials.')

    sha256 = manifest.get('sha256')
    if sha256 is None:
        raise SourceError('Manifest is missing the required field "sha256".')
    if not is_hex64(sha256):
        raise SourceError('Manifest "sha256" must be 64 lowercase hex characters.')

    size_bytes = _require_int(manifest, 'size_bytes')
    width = _require_int(manifest, 'width')
    height = _require_int(manifest, 'height')
    date = _require_str(manifest, 'date')
    published_at = _require_str(manifest, 'published_at')

    photo_id = manifest.get('photo_id')
    if photo_id is not None and not isinstance(photo_id, str):
        raise SourceError('Manifest "photo_id" must be a string or null.')
    source_photo_url = manifest.get('source_photo_url')
    if source_photo_url is not None and not isinstance(source_photo_url, str):
        raise SourceError('Manifest "source_photo_url" must be a string when present.')

    for key, value in (('width', width), ('height', height), ('size_bytes', size_bytes)):
        if value is None:
            warnings.append('Manifest does not declare %s; that check is skipped.' % key)
    if not photo_id:
        warnings.append('No photo_id (the publisher may be using its own images); '
                        'source attribution is not required.')

    return Target(mode='manifest', source_url=source_url, url=image_url, sha256=sha256,
                  size_bytes=size_bytes, width=width, height=height, date=date,
                  published_at=published_at, schema_version=schema_version,
                  manifest=manifest, warnings=warnings)


def resolve_source(config, fetcher):
    """Turn the configured source into a Target (fetches only the manifest)."""
    source = config['source']
    url = source['url'].strip()
    if source['mode'] == 'image':
        _reject_repository_page(url)
        return Target(
            mode='image', source_url=url, url=url,
            warnings=['Image mode: the source is a plain image URL with no manifest, so there is '
                      'no consistency guarantee that the bytes match what the publisher '
                      'intended, and an unchanged image may still be re-downloaded.'])
    _reject_repository_page(url)
    response = fetcher.get(url, max_bytes=MANIFEST_MAX_BYTES)
    if response.not_modified:  # pragma: no cover - only when a conditional request was made
        raise SourceError('The manifest returned 304 Not Modified without a cached copy.')
    if response.content_type in ('text/html', 'application/xhtml+xml'):
        raise SourceError(REPO_PAGE_HINT)
    return parse_manifest(response.data, source_url=url)
