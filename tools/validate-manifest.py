#!/usr/bin/env python3
"""Validate a wallpaper source before you point PaperDrop at it.

    python3 tools/validate-manifest.py https://example.com/desk.json
    python3 tools/validate-manifest.py ./desk.json

It reports what the source declares, what the standard requires, and — when the
manifest carries a sha256 — optionally confirms the image still matches it.
Nothing is downloaded to your wallpaper cache and nothing is applied.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))

from paperdrop.errors import PaperDropError  # noqa: E402
from paperdrop.fetch import Fetcher, MANIFEST_MAX_BYTES  # noqa: E402
from paperdrop.sources import parse_manifest  # noqa: E402
from paperdrop.util import looks_like_repository_page, sha256_hex  # noqa: E402
from paperdrop.verify import check_bytes, check_image  # noqa: E402


def load(source, fetcher):
    if source.startswith(('http://', 'https://')):
        if looks_like_repository_page(source):
            raise PaperDropError('That is a repository or directory page, not a manifest URL.')
        response = fetcher.get(source, max_bytes=MANIFEST_MAX_BYTES)
        if response.content_type in ('text/html', 'application/xhtml+xml'):
            raise PaperDropError('The URL returned HTML, not JSON. Point it at the manifest file.')
        return response.data, source
    return pathlib.Path(source).read_bytes(), str(source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='manifest URL or local path')
    parser.add_argument('--check-image', action='store_true',
                        help='also download the image and verify it against the manifest')
    args = parser.parse_args()

    fetcher = Fetcher(retries=1)
    try:
        payload, origin = load(args.source, fetcher)
        target = parse_manifest(payload, source_url=origin)
    except (PaperDropError, OSError) as error:
        print('INVALID: %s' % error, file=sys.stderr)
        return 1

    print('source          %s' % origin)
    print('schema version  %d' % target.schema_version)
    print('image url       %s' % target.url)
    print('sha256          %s' % target.sha256)
    print('size_bytes      %s' % (target.size_bytes if target.size_bytes else '(not declared)'))
    print('dimensions      %sx%s' % (target.width or '?', target.height or '?'))
    print('date            %s' % (target.date or '(not declared)'))
    print('published_at    %s' % (target.published_at or '(not declared)'))
    print('photo_id        %s' % target.manifest.get('photo_id'))
    print('attribution     %s' % target.manifest.get('source_photo_url', '(none)'))
    for warning in target.warnings:
        print('note            %s' % warning)

    if args.check_image:
        print('\ndownloading the image to verify it ...')
        try:
            response = fetcher.get(target.url)
            digest = check_bytes(response.data, expected_sha256=target.sha256,
                                 expected_size=target.size_bytes)
            width, height, image_format = check_image(
                response.data, expected_width=target.width, expected_height=target.height)
            print('OK  %s %dx%d sha256=%s matches the manifest'
                  % (image_format, width, height, digest))
        except PaperDropError as error:
            print('MISMATCH: %s' % error, file=sys.stderr)
            return 1
    else:
        local = pathlib.Path(origin)
        if local.is_file():
            blob = local.read_bytes()
            print('\nlocal file sha256 %s' % sha256_hex(blob))
    print('\nThis source looks usable in manifest mode.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
