"""Default transport integration tests; no public network or user proxies."""
from __future__ import annotations

import gc
import http.server
import io
import ssl
import threading
import time
import unittest
import urllib.request
from unittest import mock

import support  # noqa: F401

from paperdrop.errors import FetchError
from paperdrop.fetch import Fetcher
from paperdrop import fetch


BODY = b'complete HTTP/1.1 response'


class PersistentServer(http.server.ThreadingHTTPServer):
    # server_close joins worker threads rather than silently abandoning them.
    daemon_threads = False

    def __init__(self, *args):
        self.seen = []
        self.finished = []
        super().__init__(*args)


class PersistentHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.finished = threading.Event()
        self.server.finished.append(self.finished)
        # A faulty client must not strand a handler forever.
        self.connection.settimeout(3)

    def finish(self):
        try:
            super().finish()
        finally:
            self.finished.set()

    def do_CONNECT(self):
        self.server.seen.append((self.command, self.path, dict(self.headers)))
        self.send_response(502)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        self.server.seen.append((self.command, self.path, dict(self.headers)))
        if self.path in ('/redirect', '/redirect-body'):
            self.send_response(302)
            self.send_header('Location', '/body')
            self.send_header('Content-Length', '1024' if self.path == '/redirect-body' else '0')
            self.end_headers()
            return
        if self.path == '/disconnect-once' and sum(
                path == self.path for _, path, _ in self.server.seen) == 1:
            self.close_connection = True
            return
        if self.path in ('/404', '/304', '/503'):
            self.send_response(int(self.path[1:]))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(BODY)))
        self.end_headers()
        if self.path == '/stall':
            return
        if self.path == '/short':
            self.wfile.write(BODY[:-5])
            self.wfile.flush()
            self.close_connection = True
            return
        forced_close = self.headers.get('Connection', '').lower() == 'close'
        self.wfile.write(BODY[:-5] if forced_close else BODY)
        self.wfile.flush()
        # Without close, leave the socket open awaiting the next request.
        # The client must finish by Content-Length, NOT by waiting for EOF.
        self.close_connection = forced_close


class PersistentTransportTests(unittest.TestCase):
    def setUp(self):
        self.proxies = mock.patch('urllib.request.getproxies', return_value={})
        self.proxies.start()
        self.addCleanup(self.proxies.stop)
        self.server = PersistentServer(('127.0.0.1', 0), PersistentHandler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = 'http://127.0.0.1:%d' % self.server.server_address[1]

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def fetcher(self, **options):
        defaults = dict(allow_http=True, retries=0, timeout=1, sleep=lambda _: None)
        defaults.update(options)
        return Fetcher(**defaults)

    def assert_finished(self):
        self.assertTrue(self.server.finished)
        for finished in self.server.finished:
            self.assertTrue(finished.wait(1), 'client left a connection/handler open')

    def test_redirect_does_not_drain_an_unused_body_or_force_close(self):
        response = self.fetcher().get(self.base + '/redirect-body',
                                      headers={'Cookie': 'private', 'X-Private': 'secret'})
        self.assertEqual(response.data, BODY)
        self.assertEqual([entry[:2] for entry in self.server.seen],
                         [('GET', '/redirect-body'), ('GET', '/body')])
        for method, path, headers in self.server.seen:
            self.assertNotIn('Connection', headers)
        redirected_headers = self.server.seen[-1][2]
        self.assertNotIn('Cookie', redirected_headers)
        self.assertNotIn('X-Private', redirected_headers)
        self.assert_finished()

    def test_failed_connect_closes_socket_while_error_is_retained(self):
        self.proxies.stop()
        was_enabled = gc.isenabled()
        gc.disable()
        self.addCleanup(gc.enable if was_enabled else gc.disable)
        with mock.patch('urllib.request.getproxies', return_value={'https': self.base}):
            with mock.patch('urllib.request.proxy_bypass', return_value=False):
                with self.assertRaises(FetchError) as caught:
                    self.fetcher().get('https://origin.test/body')
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(self.server.seen[0][:2], ('CONNECT', 'origin.test:443'))
        self.assert_finished()

    def test_disconnect_before_headers_is_retried(self):
        delays = []
        result = self.fetcher(retries=1, sleep=delays.append).get(self.base + '/disconnect-once')
        self.assertEqual(result.data, BODY)
        self.assertEqual(len(self.server.seen), 2)
        self.assertEqual(delays, [1.0])
        self.assert_finished()

    def test_read_failures_and_size_rejection_close_connections(self):
        cases = [('/short', {}, True), ('/stall', {}, True),
                 ('/stall', {'max_bytes': 2}, False)]
        for path, options, retryable in cases:
            with self.subTest(path=path, options=options):
                with self.assertRaises(FetchError) as caught:
                    self.fetcher(**options).get(self.base + path)
                self.assertEqual(caught.exception.retryable, retryable)
                self.assert_finished()

    def test_http_errors_and_304_close_persistent_connections(self):
        for status in (404, 503, 304):
            with self.subTest(status=status):
                if status == 304:
                    self.assertTrue(self.fetcher().get(self.base + '/304').not_modified)
                else:
                    with self.assertRaises(FetchError) as caught:
                        self.fetcher().get(self.base + '/%d' % status)
                    self.assertEqual(caught.exception.status, status)
                    self.assertEqual(caught.exception.retryable, status == 503)
                self.assert_finished()

    def test_redirect_rejection_closes_response_without_finalizers(self):
        responses = []
        original = fetch._RedirectPolicy.redirect_request

        def capture(policy, req, fp, *args):
            responses.append(fp)
            self.addCleanup(fp.close)
            return original(policy, req, fp, *args)

        with mock.patch.object(fetch._RedirectPolicy, 'redirect_request', capture):
            with self.assertRaises(FetchError) as caught:
                self.fetcher(max_redirects=0).get(self.base + '/redirect')
        self.assertIn('Too many redirects', str(caught.exception))
        self.assertEqual(len(responses), 1)
        self.assertTrue(responses[0].closed)
        self.assert_finished()

    def test_repeated_success_and_failure_leave_no_server_handlers(self):
        for _ in range(8):
            self.assertEqual(self.fetcher().get(self.base + '/body').data, BODY)
            with self.assertRaises(FetchError):
                self.fetcher().get(self.base + '/404')
        self.assertEqual(len(self.server.finished), 16)
        self.assert_finished()

    def test_default_get_does_not_force_close_or_wait_for_persistent_eof(self):
        started = time.monotonic()
        response = self.fetcher().get(self.base + '/body')
        self.assertEqual(response.data, BODY)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(len(self.server.seen), 1)
        method, path, headers = self.server.seen[0]
        self.assertEqual((method, path), ('GET', '/body'))
        self.assertNotIn('Connection', headers)
        self.assert_finished()


class WireSocket:
    """Socket boundary double: run actual stdlib request, proxy and TLS setup."""

    def __init__(self, *replies, send_error=None):
        self.replies = list(replies)
        self.sent = []
        self.files = []
        self.close_calls = 0
        self.send_error = send_error

    def setsockopt(self, *args):
        pass

    def sendall(self, data):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)

    def makefile(self, *args):
        reply = self.replies.pop(0)
        stream = io.BytesIO(reply) if isinstance(reply, bytes) else reply
        self.files.append(stream)
        return stream

    def close(self):
        self.close_calls += 1


WIRE_BODY = b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ndata'


class HandlerRoutingTests(unittest.TestCase):
    def setUp(self):
        self.discovery = mock.patch('urllib.request.getproxies', return_value={})
        self.getproxies = self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.bypass = mock.patch('urllib.request.proxy_bypass', return_value=False)
        self.bypass.start()
        self.addCleanup(self.bypass.stop)
        self.wraps = []

        def wrap(context, sock, server_hostname=None, **kwargs):
            self.wraps.append((context, server_hostname))
            return sock

        self.tls = mock.patch.object(ssl.SSLContext, 'wrap_socket', wrap)
        self.tls.start()
        self.addCleanup(self.tls.stop)

    def assert_released(self, sock):
        self.assertGreaterEqual(sock.close_calls, 1)
        self.assertTrue(sock.files)
        self.assertTrue(all(stream.closed for stream in sock.files))

    def test_http_and_https_handlers_use_scoped_connections(self):
        for scheme in ('http', 'https'):
            with self.subTest(scheme=scheme):
                sock = WireSocket(WIRE_BODY)
                with mock.patch('socket.create_connection', return_value=sock) as connect:
                    result = Fetcher(allow_http=True, retries=0, timeout=7).get(
                        scheme + '://origin.test/picture?day=1')
                self.assertEqual(result.data, b'data')
                connect.assert_called_once_with(('origin.test', 443 if scheme == 'https' else 80),
                                                7, None)
                wire = b''.join(sock.sent)
                self.assertTrue(wire.startswith(b'GET /picture?day=1 HTTP/1.1\r\n'))
                self.assertIn(b'Host: origin.test\r\n', wire)
                self.assertNotIn(b'Connection:', wire)
                self.assertNotIn(b'Authorization:', wire)
                self.assert_released(sock)
        self.assertEqual(self.getproxies.call_count, 2)
        self.assertEqual(len(self.wraps), 1)
        context, hostname = self.wraps[0]
        self.assertEqual(hostname, 'origin.test')
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        # An unrelated urllib opener still sends its original close header.
        unrelated = WireSocket(WIRE_BODY)
        with mock.patch('socket.create_connection', return_value=unrelated):
            with urllib.request.build_opener().open('http://unrelated.test/body') as response:
                self.assertEqual(response.read(), b'data')
        self.assertIn(b'Connection: close\r\n', b''.join(unrelated.sent))
        self.assert_released(unrelated)

    def test_http_proxy_uses_absolute_target_and_standard_discovery(self):
        self.getproxies.return_value = {'http': 'http://proxy.test:8080'}
        sock = WireSocket(WIRE_BODY)
        with mock.patch('socket.create_connection', return_value=sock) as connect:
            result = Fetcher(allow_http=True, retries=0).get('http://origin.test/picture')
        self.assertEqual(result.data, b'data')
        self.getproxies.assert_called_once_with()
        self.assertEqual(connect.call_args[0][0], ('proxy.test', 8080))
        wire = b''.join(sock.sent)
        self.assertTrue(wire.startswith(b'GET http://origin.test/picture HTTP/1.1\r\n'))
        self.assertIn(b'Host: origin.test\r\n', wire)
        self.assertNotIn(b'Connection:', wire)
        self.assert_released(sock)

    def test_https_proxy_connect_keeps_credentials_out_of_origin_request(self):
        self.getproxies.return_value = {'https': 'http://user:pass@proxy.test:8080'}
        sock = WireSocket(b'HTTP/1.1 200 Connection established\r\n\r\n', WIRE_BODY)
        with mock.patch('socket.create_connection', return_value=sock) as connect:
            fetcher = Fetcher(retries=0)
            result = fetcher.get('https://origin.test/picture')
        self.assertEqual(result.data, b'data')
        self.assertEqual(connect.call_args[0][0], ('proxy.test', 8080))
        tunnel, request = sock.sent
        # CONNECT is HTTP/1.0 on 3.9 and HTTP/1.1 on newer interpreters.
        self.assertTrue(tunnel.startswith(b'CONNECT origin.test:443 HTTP/1.'))
        self.assertIn(b'Proxy-Authorization: Basic dXNlcjpwYXNz\r\n', tunnel)
        self.assertTrue(request.startswith(b'GET /picture HTTP/1.1\r\n'))
        self.assertNotIn(b'Authorization', request)
        self.assertNotIn(b'Connection:', request)
        self.assertNotIn('Proxy-Authorization', fetcher.requests[0][1])
        context, hostname = self.wraps[0]
        self.assertEqual(hostname, 'origin.test')
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assert_released(sock)

    def test_proxy_bypass_keeps_the_origin_route(self):
        self.getproxies.return_value = {'https': 'http://user:pass@proxy.test:8080'}
        sock = WireSocket(WIRE_BODY)
        with mock.patch('urllib.request.proxy_bypass', return_value=True) as bypass:
            with mock.patch('socket.create_connection', return_value=sock) as connect:
                result = Fetcher(retries=0).get('https://origin.test/picture')
        self.assertEqual(result.data, b'data')
        bypass.assert_called_once_with('origin.test')
        self.assertEqual(connect.call_args[0][0], ('origin.test', 443))
        self.assertNotIn(b'Authorization', b''.join(sock.sent))
        self.assert_released(sock)

    def test_https_proxy_redirect_uses_fresh_tunnel_without_origin_credentials(self):
        self.getproxies.return_value = {'https': 'http://user:pass@proxy.test:8080'}
        established = b'HTTP/1.1 200 Connection established\r\n\r\n'
        redirect = (b'HTTP/1.1 302 Found\r\nLocation: https://other.test/new\r\n'
                    b'Content-Length: 0\r\n\r\n')
        sockets = [WireSocket(established, redirect), WireSocket(established, WIRE_BODY)]
        with mock.patch('socket.create_connection', side_effect=sockets):
            result = Fetcher(retries=0).get('https://origin.test/picture',
                                            headers={'Cookie': 'private'})
        self.assertEqual(result.data, b'data')
        self.assertEqual(result.url, 'https://other.test/new')
        self.assertEqual([hostname for _, hostname in self.wraps], ['origin.test', 'other.test'])
        for sock, host in zip(sockets, (b'origin.test', b'other.test')):
            tunnel, request = sock.sent
            self.assertTrue(tunnel.startswith(b'CONNECT ' + host + b':443 HTTP/1.'))
            self.assertIn(b'Proxy-Authorization: Basic dXNlcjpwYXNz\r\n', tunnel)
            self.assertNotIn(b'Authorization', request)
            self.assertNotIn(b'Connection:', request)
            self.assert_released(sock)
        self.assertNotIn(b'Cookie:', sockets[-1].sent[-1])

    def test_tls_verification_failure_closes_socket(self):
        sock = WireSocket()
        with mock.patch.object(ssl.SSLContext, 'wrap_socket',
                               side_effect=ssl.SSLCertVerificationError('untrusted certificate')):
            with mock.patch('socket.create_connection', return_value=sock):
                with self.assertRaises(FetchError) as caught:
                    Fetcher(retries=0).get('https://origin.test/picture')
        self.assertIn('untrusted certificate', str(caught.exception))
        self.assertGreaterEqual(sock.close_calls, 1)
        self.assertEqual(sock.sent, [])

    def test_connect_failure_closes_socket_and_retries_are_bounded(self):
        self.getproxies.return_value = {'https': 'http://proxy.test:8080'}
        sockets = [WireSocket(b'HTTP/1.1 502 Bad Gateway\r\n\r\n') for _ in range(2)]
        delays = []
        with mock.patch('socket.create_connection', side_effect=sockets) as connect:
            with self.assertRaises(FetchError) as caught:
                Fetcher(retries=1, sleep=delays.append).get('https://origin.test/picture')
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(delays, [1.0])
        self.assertEqual(self.wraps, [])
        for sock in sockets:
            self.assert_released(sock)

    def test_send_reset_and_remote_disconnect_close_sockets_before_retry(self):
        for first in (WireSocket(send_error=ConnectionResetError('reset')),
                      WireSocket(b'')):
            with self.subTest(first=first):
                second = WireSocket(WIRE_BODY)
                with mock.patch('socket.create_connection', side_effect=[first, second]) as connect:
                    result = Fetcher(allow_http=True, retries=1, sleep=lambda _: None).get(
                        'http://origin.test/picture')
                self.assertEqual(result.data, b'data')
                self.assertEqual(connect.call_count, 2)
                self.assertGreaterEqual(first.close_calls, 1)
                self.assertTrue(all(stream.closed for stream in first.files))
                self.assert_released(second)

    def test_read_reset_closes_response_before_retry(self):
        class ResetBody(io.BytesIO):
            def read(self, *args):
                raise ConnectionResetError('body reset')

            def readinto(self, *args):
                raise ConnectionResetError('body reset')

        first = WireSocket(ResetBody(WIRE_BODY))
        second = WireSocket(WIRE_BODY)
        with mock.patch('socket.create_connection', side_effect=[first, second]):
            result = Fetcher(retries=1, sleep=lambda _: None).get('https://origin.test/picture')
        self.assertEqual(result.data, b'data')
        self.assert_released(first)
        self.assert_released(second)

    def test_injected_opener_factory_remains_the_only_transport_when_supplied(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.headers = {'Content-Length': '4'}
        response.read.side_effect = [b'data', b'']
        response.geturl.return_value = 'https://injected.test/body'
        opener = mock.Mock()
        opener.open.return_value = response
        factory = mock.Mock(return_value=opener)
        with mock.patch('urllib.request.build_opener') as build:
            result = Fetcher(opener_factory=factory).get_conditional(
                'https://injected.test/body', etag='"v1"')
        build.assert_not_called()
        factory.assert_called_once_with('https://injected.test/body', {'hops': 0})
        request = opener.open.call_args[0][0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.get_header('If-none-match'), '"v1"')
        self.assertEqual(result.data, b'data')
        response.__exit__.assert_called_once()


if __name__ == '__main__':
    unittest.main()
