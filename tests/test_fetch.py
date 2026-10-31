"""Transfer policy: retries, redirect limits, size caps, and no credentials."""
from __future__ import annotations

import http.client
import http.server
import json
import pathlib
import threading
import unittest

import support  # noqa: F401  (puts src/ on sys.path)

from paperdrop.errors import FetchError
from paperdrop.fetch import Fetcher

IMAGE = support.make_image((20, 10))


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'PaperDropTest/1.0'

    def log_message(self, *args):  # keep the test output clean
        pass

    def _send(self, status, body=b'', headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        if body:
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server API
        self.server.seen.append((self.path, dict(self.headers)))
        path = self.path.split('?')[0]
        if path == '/image':
            return self._send(200, IMAGE)
        if path == '/image-etag':
            if self.headers.get('If-None-Match') == '"v1"':
                return self._send(304, b'')
            return self._send(200, IMAGE, {'ETag': '"v1"'})
        if path == '/redirect/once':
            return self._send(302, b'', {'Location': '/image'})
        if path == '/redirect/twice':
            return self._send(302, b'', {'Location': '/redirect/once'})
        if path == '/redirect/loop':
            return self._send(302, b'', {'Location': '/redirect/loop'})
        if path == '/redirect/file':
            return self._send(302, b'', {'Location': 'file:///etc/passwd'})
        if path == '/flaky':
            self.server.flaky.append(1)
            if len(self.server.flaky) < 3:
                return self._send(503, b'', {'Retry-After': '0'})
            return self._send(200, IMAGE)
        if path == '/short-body-once':
            attempts = getattr(self.server, 'short_body_attempts', 0) + 1
            setattr(self.server, 'short_body_attempts', attempts)
            if attempts == 1:
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(IMAGE)))
                self.end_headers()
                self.wfile.write(IMAGE[:-17])
                self.wfile.flush()
                self.close_connection = True
                return
            return self._send(200, IMAGE)
        if path == '/big':
            return self._send(200, b'x' * 4096)
        if path == '/missing':
            return self._send(404, b'')
        return self._send(500, b'')


class FetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.seen, cls.server.flaky = [], []
        setattr(cls.server, 'short_body_attempts', 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def fetcher(self, **kwargs):
        options = dict(timeout=10, retries=2, max_redirects=5, max_bytes=1024 * 1024,
                       allow_http=True, sleep=lambda seconds: None, backoff_seconds=0)
        options.update(kwargs)
        return Fetcher(**options)

    def test_plain_http_is_refused_unless_explicitly_allowed(self):
        strict = Fetcher(allow_http=False)
        with self.assertRaises(FetchError) as caught:
            strict.get(self.base + '/image')
        self.assertIn('http', str(caught.exception))

    def test_credentials_in_url_are_refused(self):
        with self.assertRaises(FetchError) as caught:
            self.fetcher().get('http://user:secret@127.0.0.1:1/image')
        self.assertIn('credentials', str(caught.exception))

    def test_no_authorization_header_is_ever_sent(self):
        self.server.seen.clear()
        self.fetcher().get(self.base + '/image')
        self.server.seen.clear()
        self.fetcher().get(self.base + '/redirect/once')
        for path, headers in self.server.seen:
            self.assertNotIn('Authorization', headers, 'Path %s leaked credentials' % path)
            self.assertNotIn('Cookie', headers)

    def test_redirect_is_followed_and_body_returned(self):
        response = self.fetcher().get(self.base + '/redirect/twice')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, IMAGE)

    def test_redirect_loop_is_stopped(self):
        with self.assertRaises(FetchError) as caught:
            self.fetcher(max_redirects=3).get(self.base + '/redirect/loop')
        self.assertIn('redirect', str(caught.exception).lower())

    def test_redirect_to_another_scheme_is_refused(self):
        with self.assertRaises(FetchError) as caught:
            self.fetcher().get(self.base + '/redirect/file')
        self.assertIn('scheme', str(caught.exception))

    def test_transient_failure_is_retried_then_succeeds(self):
        self.server.flaky.clear()
        response = self.fetcher().get(self.base + '/flaky')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, IMAGE)

    def test_truncated_content_length_response_is_retried_then_succeeds(self):
        setattr(self.server, 'short_body_attempts', 0)
        response = self.fetcher(retries=1).get(self.base + '/short-body-once')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, IMAGE)
        self.assertEqual(getattr(self.server, 'short_body_attempts'), 2)

    def test_silent_short_read_against_content_length_is_retryable(self):
        class ShortReadResponse:
            headers = {'Content-Length': str(len(IMAGE))}

            def __init__(self):
                self.sent = False

            def read(self, amount):
                if self.sent:
                    return b''
                self.sent = True
                return IMAGE[:-17]

        with self.assertRaises(FetchError) as caught:
            self.fetcher()._read_bounded(ShortReadResponse(), 1024 * 1024)
        self.assertTrue(caught.exception.retryable)
        self.assertIn(str(len(IMAGE)), str(caught.exception))
        self.assertIn(str(len(IMAGE) - 17), str(caught.exception))

    def test_incomplete_read_exception_is_retryable(self):
        class IncompleteResponse:
            headers = {'Content-Length': str(len(IMAGE))}

            def read(self, amount):
                raise http.client.IncompleteRead(IMAGE[:-17], 17)

        with self.assertRaises(FetchError) as caught:
            self.fetcher()._read_bounded(IncompleteResponse(), 1024 * 1024)
        self.assertTrue(caught.exception.retryable)
        self.assertIn(str(len(IMAGE) - 17), str(caught.exception))
        self.assertIn(str(len(IMAGE)), str(caught.exception))

    def test_non_ascii_content_length_is_rejected_as_fetch_error(self):
        class InvalidLengthResponse:
            headers = {'Content-Length': '²'}

            def read(self, amount):
                return IMAGE

        with self.assertRaises(FetchError) as caught:
            self.fetcher()._read_bounded(InvalidLengthResponse(), 1024 * 1024)
        self.assertFalse(caught.exception.retryable)
        self.assertIn('Content-Length', str(caught.exception))

    def test_permanent_failure_is_not_retried(self):
        before = len(self.server.seen)
        with self.assertRaises(FetchError) as caught:
            self.fetcher().get(self.base + '/missing')
        self.assertEqual(caught.exception.status, 404)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(len(self.server.seen) - before, 1, '404 must not be retried')

    def test_body_over_the_limit_is_refused(self):
        with self.assertRaises(FetchError) as caught:
            self.fetcher(max_bytes=512).get(self.base + '/big')
        self.assertIn('512', str(caught.exception))

    def test_conditional_request_reports_not_modified(self):
        response = self.fetcher().get_conditional(self.base + '/image-etag', etag='"v1"')
        self.assertTrue(response.not_modified)

    def test_conditional_request_returns_body_on_change(self):
        response = self.fetcher().get_conditional(self.base + '/image-etag', etag='"other"')
        self.assertFalse(response.not_modified)
        self.assertEqual(response.data, IMAGE)

    def test_recorded_requests_never_contain_credentials(self):
        fetcher = self.fetcher()
        fetcher.get(self.base + '/image')
        for url, headers in fetcher.requests:
            self.assertNotIn('Authorization', headers)
            self.assertNotIn('@', url.split('//', 1)[-1].split('/')[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
