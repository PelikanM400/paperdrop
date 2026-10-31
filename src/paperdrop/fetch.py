"""HTTP transfer with a fixed, conservative policy.

- only http(s), and plain http only when the user explicitly allowed it
- redirects are followed at most N times and never carry credentials
- the body is read in bounded chunks so a wrong URL cannot exhaust memory
- retries apply only to transient failures (timeouts, 429, 5xx)
"""
from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from . import USER_AGENT
from .errors import FetchError

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
MANIFEST_MAX_BYTES = 262144
_CHUNK = 65536


@dataclass
class Response:
    url: str
    status: int
    data: bytes = b''
    headers: dict = field(default_factory=dict)

    @property
    def not_modified(self):
        return self.status == 304

    @property
    def content_type(self):
        return (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()

    @property
    def etag(self):
        return self.headers.get('ETag')

    @property
    def last_modified(self):
        return self.headers.get('Last-Modified')


class _RedirectPolicy(urllib.request.HTTPRedirectHandler):
    """Count hops, refuse non-approved schemes, and never forward credentials."""

    def __init__(self, allowed_schemes, max_redirects, state):
        self.allowed_schemes = allowed_schemes
        self.max_redirects = max_redirects
        self.state = state

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.state['hops'] += 1
        if self.state['hops'] > self.max_redirects:
            raise FetchError('Too many redirects (limit %d).' % self.max_redirects)
        parts = urllib.parse.urlsplit(newurl)
        if parts.scheme not in self.allowed_schemes:
            raise FetchError('Refusing a redirect to scheme "%s".' % parts.scheme)
        if parts.username or parts.password:
            raise FetchError('Refusing a redirect to a URL with embedded credentials.')
        # Build a fresh request: no Authorization, no cookies, no original headers.
        return urllib.request.Request(newurl, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'},
                                      method='GET')


class Fetcher:
    def __init__(self, timeout=60, retries=3, max_redirects=5, max_bytes=67108864,
                 allow_http=False, sleep=time.sleep, backoff_seconds=1.0,
                 max_backoff_seconds=8.0, opener_factory=None):
        self.timeout = timeout
        self.retries = retries
        self.max_redirects = max_redirects
        self.max_bytes = max_bytes
        self.allow_http = allow_http
        self._sleep = sleep
        self.backoff_seconds = backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._opener_factory = opener_factory
        self.requests = []  # (url, headers) actually attempted; headers never carry secrets

    @property
    def allowed_schemes(self):
        return ('http', 'https') if self.allow_http else ('https',)

    @classmethod
    def from_config(cls, config, **overrides):
        http = config['http']
        options = dict(timeout=http['timeout_seconds'], retries=http['retries'],
                       max_redirects=http['max_redirects'], max_bytes=http['max_bytes'],
                       allow_http=http['allow_http'])
        options.update(overrides)
        return cls(**options)

    def get(self, url, headers=None, max_bytes=None):
        return self._with_retries(url, headers, max_bytes)

    def get_conditional(self, url, etag=None, last_modified=None, max_bytes=None):
        headers = {}
        if etag:
            headers['If-None-Match'] = etag
        if last_modified:
            headers['If-Modified-Since'] = last_modified
        return self._with_retries(url, headers or None, max_bytes)

    def _with_retries(self, url, headers, max_bytes):
        attempts = max(1, int(self.retries) + 1)
        delay = self.backoff_seconds
        for attempt in range(1, attempts + 1):
            try:
                return self._once(url, headers=headers, max_bytes=max_bytes)
            except FetchError as error:
                if not error.retryable or attempt == attempts:
                    raise
                wait = error.retry_after if error.retry_after else delay
                self._sleep(min(max(wait, 0), self.max_backoff_seconds))
                delay *= 2
        raise FetchError('Unreachable retry state.')  # pragma: no cover

    def _once(self, url, headers=None, max_bytes=None):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in self.allowed_schemes:
            raise FetchError('Refusing to fetch a "%s" URL.' % parts.scheme)
        if parts.username or parts.password:
            raise FetchError('Refusing a URL with embedded credentials.')
        request_headers = {'User-Agent': USER_AGENT, 'Accept': '*/*'}
        if headers:
            request_headers.update(headers)
        assert 'Authorization' not in request_headers, 'PaperDrop never sends credentials'
        self.requests.append((url, dict(request_headers)))
        state = {'hops': 0}
        if self._opener_factory is not None:
            opener = self._opener_factory(url, state)
        else:
            policy = _RedirectPolicy(self.allowed_schemes, self.max_redirects, state)
            opener = urllib.request.build_opener(policy)
        request = urllib.request.Request(url, headers=request_headers, method='GET')
        try:
            with opener.open(request, timeout=self.timeout) as response:
                status = getattr(response, 'status', None) or 200
                data = self._read_bounded(response, max_bytes)
                return Response(url=response.geturl(), status=status, data=data,
                                headers={key: value for key, value in response.headers.items()})
        except FetchError:
            raise
        except urllib.error.HTTPError as error:
            status = error.code
            retry_after = None
            location = None
            if error.headers is not None:
                raw = error.headers.get('Retry-After')
                if raw and raw.strip().isdigit():
                    retry_after = int(raw.strip())
                location = error.headers.get('Location')
            error.close()
            if status == 304:
                return Response(url=url, status=304, data=b'', headers={})
            if 300 <= status < 400:
                # A redirect we could not follow: urllib refuses some schemes before our
                # own policy sees them, so report the reason in the same plain wording.
                scheme = urllib.parse.urlsplit(location).scheme if location else ''
                if scheme and scheme not in self.allowed_schemes:
                    raise FetchError('Refusing a redirect to scheme "%s".' % scheme,
                                     status=status)
                raise FetchError('Refusing a redirect (HTTP %d) to %s'
                                 % (status, location or 'an unknown location'), status=status)
            raise FetchError('HTTP %d for %s' % (status, url),
                             retryable=status in RETRYABLE_STATUS, status=status,
                             retry_after=retry_after) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise FetchError('Network error for %s: %s' % (url, error), retryable=True) from None

    def _read_bounded(self, response, max_bytes):
        limit = self.max_bytes if max_bytes is None else max_bytes
        declared = response.headers.get('Content-Length')
        if declared is None:
            expected = None
        else:
            normalized_length = declared.strip()
            if not normalized_length.isascii() or not normalized_length.isdigit():
                raise FetchError('Invalid Content-Length header.',
                                 status=getattr(response, 'status', None))
            expected = int(normalized_length)
        if expected is not None and expected > limit:
            raise FetchError('Refusing a %s byte body (limit %d).' % (declared, limit))
        chunks = []
        total = 0
        while True:
            try:
                chunk = response.read(_CHUNK)
            except http.client.IncompleteRead as error:
                received = total + len(error.partial or b'')
                if received > limit:
                    raise FetchError('Response exceeded the %d byte limit.' % limit)
                if expected is not None:
                    message = ('Incomplete response body: received %d bytes, Content-Length '
                               'declared %d.' % (received, expected))
                else:
                    message = 'Incomplete response body: stream ended after %d bytes.' % received
                raise FetchError(message, retryable=True,
                                 status=getattr(response, 'status', None)) from None
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise FetchError('Response exceeded the %d byte limit.' % limit)
            chunks.append(chunk)
        if expected is not None and total != expected:
            raise FetchError('Incomplete response body: received %d bytes, Content-Length '
                             'declared %d.' % (total, expected), retryable=True,
                             status=getattr(response, 'status', None))
        return b''.join(chunks)
