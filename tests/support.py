"""Shared test helpers. No test in this suite touches the real network."""
from __future__ import annotations

import io
import json
import logging
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = pathlib.Path(__file__).parent / 'fixtures'

from paperdrop.errors import FetchError  # noqa: E402
from paperdrop.fetch import Response  # noqa: E402
from paperdrop.util import sha256_hex  # noqa: E402


def make_image(size=(60, 40), color='red', fmt='JPEG'):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def fixture(name):
    return (FIXTURES / name).read_bytes()


def silent_logger():
    """A logger that swallows everything, so test output stays readable."""
    logger = logging.getLogger('paperdrop-test')
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def manifest_for(image_bytes, **overrides):
    payload = {
        'date': '2030-01-02',
        'published_at': '2030-01-02T04:30:00+08:00',
        'timezone': 'Asia/Shanghai',
        'photo_id': 'abcdefghijk',
        'image_url': 'https://example.test/image.jpg',
        'sha256': sha256_hex(image_bytes),
        'width': 60,
        'height': 40,
        'size_bytes': len(image_bytes),
    }
    payload.update(overrides)
    return json.dumps(payload).encode('utf-8')


class FakeFetcher:
    """A deterministic stand-in for Fetcher, recording what was requested."""

    def __init__(self, responses=None, conditional=None, errors=None, max_bytes=67108864):
        self.responses = dict(responses or {})
        self.conditional = dict(conditional or {})
        self.errors = dict(errors or {})
        self.requests = []
        self.max_bytes = max_bytes
        self.last_etag = None
        self.last_last_modified = None

    def _lookup(self, table, url):
        value = table.get(url)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, list):
            value = value.pop(0)
        return value

    def get(self, url, headers=None, max_bytes=None):
        self.requests.append(('GET', url))
        if url in self.errors:
            raise self.errors[url]
        response = self._lookup(self.responses, url)
        if response is None:
            raise FetchError('No fake response for %s' % url)
        if isinstance(response, bytes):
            response = Response(url=url, status=200, data=response,
                                headers={'Content-Type': 'image/jpeg'})
        if isinstance(response, str):
            response = Response(url=url, status=200, data=response.encode('utf-8'),
                                headers={'Content-Type': 'application/json'})
        self.last_etag = response.etag
        self.last_last_modified = response.last_modified
        return response

    def get_conditional(self, url, etag=None, last_modified=None, max_bytes=None):
        self.requests.append(('CONDITIONAL', url))
        if url in self.conditional:
            response = self.conditional[url]
            if isinstance(response, bytes):
                response = Response(url=url, status=200, data=response,
                                    headers={'Content-Type': 'image/jpeg'})
        elif url in self.responses:
            return Response(url=url, status=304, data=b'', headers={})
        else:
            raise FetchError('No fake response for %s' % url)
        self.last_etag = response.etag
        self.last_last_modified = response.last_modified
        return response


class FakePlatform:
    """Records applied paths; `reports` controls what `current()` returns."""

    name = 'fake'
    supports_screen_watch = False

    def __init__(self, reports=None, apply_effective=True):
        self.applied = []
        self.reports = reports
        self.apply_effective = apply_effective
        self.fail_with = None
        self.apply_calls = 0

    def apply(self, image_path):
        self.apply_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        self.applied.append(str(image_path))
        if self.apply_effective:
            self.reports = str(image_path)
        return [str(image_path)]

    def current(self):
        return self.reports

    def doctor(self):
        return []

    def status_lines(self):
        return ['platform: fake']


def write_config(directory, url, mode='manifest', **overrides):
    from paperdrop.config import validate
    config = {
        'source': {'mode': mode, 'url': url,
                   'image_mode_acknowledged': mode == 'image'},
        'cache_dir': str(directory / 'cache'),
        'state_file': str(directory / 'state' / 'state.json'),
        'log': {'path': str(directory / 'state' / 'paperdrop.log')},
        'http': {'retries': 1},
    }
    for key, value in overrides.items():
        config[key] = value
    validated = validate(config)
    path = directory / 'config.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return validated, path
