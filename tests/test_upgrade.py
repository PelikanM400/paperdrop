"""Credential-free upgrade tests: isolated loopback fixtures, no host installation."""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
from email.message import Message
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import unittest
import urllib.error
import urllib.parse
import urllib.request
import venv

BASE = 'https://github.com/PelikanM400/paperdrop/releases/download/v0.3.0/'
WHEEL = 'paperdrop-0.3.0-py3-none-any.whl'
DIGEST = 'a' * 64


def release():
    return {'tag_name': 'v0.3.0', 'draft': False, 'prerelease': True,
            'assets': [{'name': name, 'browser_download_url': BASE + name}
                       for name in (WHEEL, 'SHA256SUMS')]}

import support  # adds src to sys.path


class ReleaseSelectionTests(unittest.TestCase):
    def test_selects_highest_numeric_release_including_github_prereleases(self):
        from paperdrop.upgrade import _select_release
        releases = [
            {'tag_name': 'v0.2.9', 'draft': False, 'prerelease': False},
            {'tag_name': 'v0.10.0', 'draft': False, 'prerelease': True},
            {'tag_name': 'v1.0.0rc1', 'draft': False, 'prerelease': True},
            {'tag_name': 'v9.0.0', 'draft': True, 'prerelease': False},
        ]
        self.assertEqual(_select_release(releases, '0.2.3')['tag_name'], 'v0.10.0')

    def test_equal_or_older_never_selected(self):
        from paperdrop.upgrade import _select_release
        self.assertIsNone(_select_release([release()], '0.3.0'))
        self.assertIsNone(_select_release([release()], '1.0.0'))

    def test_malformed_remote_types_fail_closed(self):
        from paperdrop.upgrade import UpgradeError, _select_release
        for payload in ({}, None, 'releases', [None], [1], [{'tag_name': 3}],
                        [dict(release(), draft='false')],
                        [dict(release(), prerelease=0)]):
            with self.subTest(payload=payload), self.assertRaises(UpgradeError):
                _select_release(payload, '0.2.3')

    def test_version_tags_must_be_canonical_ascii_triplets(self):
        from paperdrop.upgrade import _select_release
        for tag in ('v01.0.0', 'v1.0', '1.0.0', 'v1.0.0\n', 'v١.0.0',
                    'v1.0.0-beta', 'v1.0.0+local', '../v1.0.0'):
            with self.subTest(tag=tag):
                self.assertIsNone(_select_release([dict(release(), tag_name=tag)], '0.2.3'))


class ArtifactTests(unittest.TestCase):
    def test_official_wheel_and_unique_checksum_are_required(self):
        from paperdrop.upgrade import _release_assets, _checksum
        wheel_url, sums_url, name = _release_assets(release())
        self.assertEqual((wheel_url, sums_url, name), (BASE + WHEEL, BASE + 'SHA256SUMS', WHEEL))
        data = (DIGEST + '  ' + WHEEL + '\n' + 'b' * 64 + ' *paperdrop-0.3.0.tar.gz\n').encode()
        self.assertEqual(_checksum(data, name), DIGEST)

    def test_untrusted_asset_urls_names_and_types_are_rejected(self):
        from paperdrop.upgrade import UpgradeError, _release_assets
        mutations = [None, {}, 'assets', [None],
                     [release()['assets'][0]],
                     release()['assets'] + [release()['assets'][0]],
                     release()['assets'] + [{'name': WHEEL, 'browser_download_url': BASE + WHEEL}]]
        for assets in mutations:
            with self.subTest(assets=assets), self.assertRaises(UpgradeError):
                _release_assets(dict(release(), assets=assets))
        for url in (BASE + WHEEL + '?token=secret', BASE + WHEEL + '#sha256=x',
                    BASE.replace('https:', 'http:') + WHEEL,
                    BASE.replace('github.com', 'user:secret@github.com') + WHEEL,
                    BASE.replace('github.com', 'github.com.evil.test') + WHEEL,
                    BASE.replace('github.com', 'github.com:443') + WHEEL,
                    BASE.replace('/v0.3.0/', '/v0.2.0/') + WHEEL,
                    'file:///etc/passwd', '/tmp/wheel.whl', 1, None):
            item = release()
            item['assets'][0]['browser_download_url'] = url
            with self.subTest(url=url), self.assertRaises(UpgradeError):
                _release_assets(item)
        for name in ('../' + WHEEL, WHEEL + '\n', WHEEL.upper(), 1, None):
            item = release()
            item['assets'][0]['name'] = name
            with self.subTest(name=name), self.assertRaises(UpgradeError):
                _release_assets(item)

    def test_checksum_missing_duplicate_bad_hash_or_path_rejected(self):
        from paperdrop.upgrade import UpgradeError, _checksum
        for text in ('', 'x' * 64 + '  ' + WHEEL, 'a' * 63 + '  ' + WHEEL,
                     DIGEST + '  ../' + WHEEL, DIGEST + '  /tmp/' + WHEEL,
                     DIGEST + '  ' + WHEEL + '\n' + DIGEST + '  ' + WHEEL,
                     DIGEST + '  ' + WHEEL + '\n' + 'b' * 64 + '  ' + WHEEL,
                     DIGEST + '  ' + WHEEL + '\nmalformed', '\x00', '哈希'):
            with self.subTest(text=text), self.assertRaises(UpgradeError):
                _checksum(text.encode(), WHEEL)


class PipxFixture(unittest.TestCase):
    def setUp(self):
        from paperdrop import upgrade as u
        self.u = u
        scratch = Path(os.environ.get('TMPDIR', '/opt/data/profiles/dedalus/cache/scratch'))
        self.directory = tempfile.TemporaryDirectory(prefix='paperdrop-upgrade-', dir=str(scratch))
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.prefix = self.root / 'pipx' / 'venvs' / 'paperdrop'
        self.bin = self.prefix / 'bin'
        self.bin.mkdir(parents=True)
        self.python = self.bin / 'python'
        self.python.write_text('#!/bin/sh\nexit 99\n')
        self.python.chmod(0o700)
        self.site = self.prefix / 'lib' / 'python3.9' / 'site-packages'
        self.module = self.site / 'paperdrop' / 'upgrade.py'
        self.module.parent.mkdir(parents=True)
        self.module.write_text('')
        self.metadata = self.prefix / 'pipx_metadata.json'
        self.meta = {'pipx_metadata_version': '0.5', 'main_package': {
            'package': 'paperdrop', 'suffix': '', 'package_version': u.__version__},
            'injected_packages': {}}
        self.metadata.write_text(json.dumps(self.meta))
        self.direct_url = None
        self.dist = SimpleNamespace(version=u.__version__, locate_file=lambda path: self.site / path,
                                    read_text=lambda name: self.direct_url)
        self.pipx = self.root / 'pipx-command'
        self.pipx.write_text('#!/bin/sh\nexit 99\n')
        self.pipx.chmod(0o700)
        for target, value in (('sys.prefix', str(self.prefix)),
                              ('sys.executable', str(self.python)), ('__file__', str(self.module))):
            p = patch('paperdrop.upgrade.' + target, value)
            p.start()
            self.addCleanup(p.stop)
        for target, value in (('metadata.distribution', self.dist), ('shutil.which', str(self.pipx))):
            p = patch('paperdrop.upgrade.' + target, return_value=value)
            p.start()
            self.addCleanup(p.stop)


class EnvironmentTests(PipxFixture):
    def test_accepts_only_canonical_pipx_main_package(self):
        env = self.u._installation()
        self.assertEqual(env['python'], str(self.python))
        self.assertEqual(env['pipx'], str(self.pipx))
        self.assertEqual(env['home'], str(self.prefix.parent.parent))

    def test_missing_corrupt_foreign_or_suffixed_metadata_rejected(self):
        for value in (None, '', 'null', '[]', '{', '{}',
                      json.dumps({'package': 'paperdrop'}),
                      json.dumps({'main_package': None}),
                      json.dumps({'main_package': {'package': 'other'},
                                  'injected_packages': {'paperdrop': {}}}),
                      json.dumps({'main_package': {'package': 'paperdrop', 'suffix': '-test'}})):
            if value is None:
                self.metadata.unlink(missing_ok=True)
            else:
                self.metadata.write_text(value)
            with self.subTest(value=value), self.assertRaises(self.u.UpgradeError):
                self.u._installation()

    def test_system_python_editable_external_code_and_no_pipx_rejected(self):
        for target, value in (('sys.base_prefix', str(self.prefix)),
                              ('__file__', str(self.root / 'checkout' / 'upgrade.py')),
                              ('sys.executable', str(self.root / 'python'))):
            with self.subTest(target=target), patch('paperdrop.upgrade.' + target, value):
                with self.assertRaises(self.u.UpgradeError):
                    self.u._installation()
        with patch('paperdrop.upgrade.shutil.which', return_value=None):
            with self.assertRaises(self.u.UpgradeError):
                self.u._installation()
        for direct_url in ('{', '[]', json.dumps({'dir_info': {'editable': True}})):
            self.direct_url = direct_url
            with self.subTest(direct_url=direct_url), self.assertRaises(self.u.UpgradeError):
                self.u._installation()
        self.direct_url = None
        self.dist.version = '0.0.0'
        with self.assertRaises(self.u.UpgradeError):
            self.u._installation()

    def test_metadata_symlink_and_oversize_fail_closed(self):
        external = self.root / 'external.json'
        external.write_text(self.metadata.read_text())
        self.metadata.unlink()
        self.metadata.symlink_to(external)
        with self.assertRaises(self.u.UpgradeError):
            self.u._installation()
        self.metadata.unlink()
        self.metadata.write_bytes(b' ' * (1024 * 1024 + 1))
        with self.assertRaises(self.u.UpgradeError):
            self.u._installation()


class NetworkTests(unittest.TestCase):
    def response(self, body=b'{}', kind='application/json', length=None):
        response = io.BytesIO(body)
        response.headers = Message()
        response.headers['Content-Type'] = kind
        if length is not None:
            response.headers['Content-Length'] = str(length)
        response.status = 200
        return response

    def download(self, response, url=None, limit=1024, kind='json'):
        from paperdrop import upgrade as u
        opener = MagicMock()
        opener.open.return_value = response
        with patch('paperdrop.upgrade.urllib.request.build_opener', return_value=opener) as build:
            result = u._download(url or u.RELEASES_URL, limit, kind)
        self.assertGreater(opener.open.call_args.kwargs['timeout'], 0)
        request = opener.open.call_args.args[0]
        self.assertNotIn('Authorization', request.headers)
        self.assertTrue(any(isinstance(arg, urllib.request.ProxyHandler) and arg.proxies == {}
                            for arg in build.call_args.args))
        return result

    def test_download_is_bounded_and_has_no_proxy_or_authorization(self):
        self.assertEqual(self.download(self.response(b'[]', length=2)), b'[]')
        self.assertEqual(self.download(self.response(b'wheel', 'application/octet-stream'),
                                       BASE + WHEEL, kind='wheel'), b'wheel')

    def test_bad_type_length_oversize_or_truncation_rejected(self):
        from paperdrop.upgrade import UpgradeError
        for body, kind, length in ((b'<html>', 'text/html', None),
                                   (b'{}', 'application/json', 1025),
                                   (b'{}', 'application/json', 'bad'),
                                   (b'{}', 'application/json', -1),
                                   (b'{}', 'application/json', 3),
                                   (b'x' * 1025, 'application/json', None)):
            with self.subTest(kind=kind, length=length), self.assertRaises(UpgradeError):
                self.download(self.response(body, kind, length))

    def test_initial_url_and_redirects_cannot_escape_official_https(self):
        from paperdrop import upgrade as u
        for url in ('http://github.com/x', 'file:///etc/passwd', 'https://evil.test/',
                    u.RELEASES_URL + '&token=secret', BASE + '../' + WHEEL,
                    BASE + WHEEL + '?token=secret'):
            with self.subTest(url=url), self.assertRaises(u.UpgradeError):
                u._download(url, 1024, 'wheel')
        redirect = u._ReleaseRedirect()
        request = urllib.request.Request(BASE + WHEEL)
        headers = Message()
        for url in ('http://release-assets.githubusercontent.com/x',
                    'https://user:secret@release-assets.githubusercontent.com/x',
                    'https://release-assets.githubusercontent.com:443/x',
                    'https://evil.test/x', 'file:///etc/passwd',
                    'https://github.com/other/repo/releases/download/v1/x',
                    'https://api.github.com/?token=secret'):
            with self.subTest(url=url), self.assertRaises(u.UpgradeError):
                redirect.redirect_request(request, None, 302, 'redirect', headers, url)
        allowed = 'https://release-assets.githubusercontent.com/x?sig=secret'
        self.assertEqual(redirect.redirect_request(request, None, 302, 'redirect', headers,
                                                  allowed).full_url, allowed)

    def test_download_failure_messages_never_expose_remote_details(self):
        from paperdrop import upgrade as u
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError('token=SECRET https://x?sig=SECRET')
        with patch('paperdrop.upgrade.urllib.request.build_opener', return_value=opener):
            with self.assertRaises(u.UpgradeError) as error:
                u._download(u.RELEASES_URL, 1024, 'json')
        self.assertNotIn('SECRET', str(error.exception))


class UpgradeFlowTests(PipxFixture):
    def setUp(self):
        version = patch('paperdrop.upgrade.__version__', '0.2.3')
        version.start()
        self.addCleanup(version.stop)
        super().setUp()
        self.wheel = b'isolated test wheel bytes'
        digest = hashlib.sha256(self.wheel).hexdigest()
        self.payloads = {self.u.RELEASES_URL: json.dumps([release()]).encode(),
                         BASE + 'SHA256SUMS': (digest + '  ' + WHEEL + '\n').encode(),
                         BASE + WHEEL: self.wheel}
        self.downloads = []
        def download(url, limit, kind):
            self.downloads.append((url, limit, kind))
            return self.payloads[url]
        self.download = patch('paperdrop.upgrade._download', side_effect=download)
        self.download.start()
        self.addCleanup(self.download.stop)
        self.probe = json.dumps({'version': '0.3.0', 'module_version': '0.3.0',
                                 'prefix': str(self.prefix),
                                 'module': str(self.module.parent / '__init__.py')}).encode()
        self.calls = []
        def run(argv, env, timeout, capture=False):
            self.calls.append((argv, env, timeout, capture))
            return self.probe if capture else b''
        p = patch('paperdrop.upgrade._run_command', side_effect=run, create=True)
        p.start()
        self.addCleanup(p.stop)

    def test_installs_hash_pinned_official_url_then_checks_new_interpreter(self):
        config = self.root / 'config.json'
        config.write_bytes(b'{"source":"private", "schedule":{"time":"09:20"}, "paused":true}')
        service = self.root / 'paperdrop.service'
        service.write_bytes(b'ExecStart=original\n')
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (config, service)}
        with patch.dict(os.environ, {'GH_TOKEN': 'SECRET', 'HTTPS_PROXY': 'https://user:SECRET@host',
                                     'PIP_INDEX_URL': 'https://user:SECRET@host',
                                     'PYTHONPATH': '/old/checkout'}):
            result = self.u.upgrade_software()
        self.assertEqual(result['ok'], True)
        self.assertEqual(result['changed'], True)
        self.assertEqual(result['version'], '0.3.0')
        self.assertEqual(result['python'], str(self.python))
        self.assertEqual(len(self.calls), 2)
        install, env, timeout, capture = self.calls[0]
        self.assertEqual(install[:3], [str(self.pipx), 'install', '--force'])
        pinned = BASE + WHEEL + '#sha256=' + hashlib.sha256(self.wheel).hexdigest()
        self.assertEqual(install[-1], pinned)
        self.assertEqual(sum(arg.startswith('--pip-args=') for arg in install), 1)
        self.assertIn('--no-deps', install[3])
        self.assertIn('--no-index', install[3])
        self.assertFalse(capture)
        self.assertGreater(timeout, 0)
        self.assertEqual(env['PIPX_HOME'], str(self.prefix.parent.parent))
        self.assertEqual(env['PIP_CONFIG_FILE'], os.devnull)
        self.assertEqual(env['NETRC'], os.devnull)
        self.assertNotIn('SECRET', repr(env))
        self.assertNotIn('PYTHONPATH', env)
        self.assertEqual(self.calls[1][0][:4], [str(self.python), '-I', '-B', '-c'])
        self.assertTrue(self.calls[1][3])
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def test_no_new_release_is_success_without_downloading_or_installing(self):
        self.payloads[self.u.RELEASES_URL] = json.dumps([
            dict(release(), tag_name='v' + self.u.__version__)]).encode()
        result = self.u.upgrade_software()
        self.assertEqual(result, {'ok': True, 'changed': False, 'version': self.u.__version__,
                                  'python': str(self.python)})
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.downloads), 1)

    def test_bad_download_hash_or_json_never_installs(self):
        for url, data in ((BASE + WHEEL, b'tampered'),
                          (BASE + 'SHA256SUMS', b''),
                          (self.u.RELEASES_URL, b'not json'),
                          (self.u.RELEASES_URL, b'null')):
            previous = self.payloads[url]
            self.payloads[url] = data
            with self.subTest(url=url), self.assertRaises(self.u.UpgradeError):
                self.u.upgrade_software()
            self.assertEqual(self.calls, [])
            self.payloads[url] = previous
        with patch('paperdrop.upgrade._download', side_effect=self.u.UpgradeError('Download failed')):
            with self.assertRaises(self.u.UpgradeError):
                self.u.upgrade_software()
        self.assertEqual(self.calls, [])

    def test_exit_zero_is_not_success_without_exact_new_version_and_prefix(self):
        for probe in (b'', b'not json', b'null', b'{}',
                      json.dumps({'version': self.u.__version__}).encode(),
                      json.dumps({'version': '0.4.0'}).encode(),
                      json.dumps({'version': '0.3.0', 'module_version': self.u.__version__,
                                  'prefix': str(self.prefix), 'module': str(self.module)}).encode(),
                      json.dumps({'version': '0.3.0', 'module_version': '0.3.0',
                                  'prefix': '/other', 'module': str(self.module)}).encode()):
            self.probe = probe
            with self.subTest(probe=probe), self.assertRaises(self.u.UpgradeError):
                self.u.upgrade_software()

    def test_install_or_probe_failure_never_returns_success(self):
        with patch('paperdrop.upgrade._run_command', side_effect=self.u.UpgradeError('Install failed')):
            with self.assertRaises(self.u.UpgradeError):
                self.u.upgrade_software()
        with patch('paperdrop.upgrade._run_command', side_effect=[b'', self.u.UpgradeError('Probe failed')]):
            with self.assertRaises(self.u.UpgradeError):
                self.u.upgrade_software()

    def test_unsafe_environment_is_rejected_before_network(self):
        self.metadata.unlink()
        with self.assertRaises(self.u.UpgradeError):
            self.u.upgrade_software()
        self.assertEqual(self.downloads, [])
        self.assertEqual(self.calls, [])


class ProcessTests(unittest.TestCase):
    def setUp(self):
        from paperdrop import upgrade as u
        self.u = u
        self.env = u._command_env({'home': '/nonexistent/paperdrop-test-pipx'})

    def command(self, code, **kwargs):
        return self.u._run_command([sys.executable, '-I', '-B', '-c', code],
                                   self.env, timeout=kwargs.pop('timeout', 5), **kwargs)

    def test_real_subprocess_success_and_bounded_capture(self):
        self.assertEqual(self.command('print("0.3.0")', capture=True), b'0.3.0\n')
        self.assertEqual(self.command('print("discarded")'), b'')

    def test_failed_or_missing_command_does_not_leak_child_output(self):
        with self.assertRaises(self.u.UpgradeError) as error:
            self.command('import sys; print("token=SECRET"); print("SECRET", file=sys.stderr); sys.exit(7)',
                         capture=True)
        self.assertNotIn('SECRET', str(error.exception))
        with self.assertRaises(self.u.UpgradeError):
            self.u._run_command(['/does/not/exist'], self.env, timeout=1)

    def test_output_flood_and_both_timeout_modes_fail(self):
        with self.assertRaises(self.u.UpgradeError):
            self.command('print("x" * 100000)', capture=True)
        for capture in (False, True):
            with self.subTest(capture=capture), self.assertRaises(self.u.UpgradeError):
                self.command('import time; time.sleep(5)', timeout=0.1, capture=capture)

    def test_timeout_kills_descendants_not_only_pipx_parent(self):
        scratch = Path(os.environ.get('TMPDIR', '/opt/data/profiles/dedalus/cache/scratch'))
        with tempfile.TemporaryDirectory(prefix='paperdrop-process-', dir=str(scratch)) as directory:
            marker = Path(directory) / 'must-not-exist'
            child = 'import time,pathlib; time.sleep(0.5); pathlib.Path(%r).write_text("late install")' % str(marker)
            code = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-I","-B","-c",%r]); time.sleep(5)' % child
            with self.assertRaises(self.u.UpgradeError):
                self.command(code, timeout=0.1)
            time.sleep(0.7)
            self.assertFalse(marker.exists())


class IsolatedIntegrationTests(unittest.TestCase):
    """Real loopback HTTP + real venv/probe + explicit fake pipx, never host pipx."""
    def test_real_pipeline_success_failure_noop_and_user_files_unchanged(self):
        from paperdrop import upgrade as u
        scratch = Path(os.environ.get('TMPDIR', '/opt/data/profiles/dedalus/cache/scratch'))
        with tempfile.TemporaryDirectory(prefix='paperdrop-integration-', dir=str(scratch)) as directory:
            root = Path(directory)
            prefix = root / 'pipx' / 'venvs' / 'paperdrop'
            venv.EnvBuilder(with_pip=False, symlinks=True).create(str(prefix))
            python = prefix / 'bin' / 'python'
            site = Path(subprocess.check_output([str(python), '-I', '-B', '-c',
                        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
            package = site / 'paperdrop'
            package.mkdir()
            # Install the real modules used by this slice, including its package-
            # local ownership helper. Copying upgrade.py alone is not a package.
            for module in ('upgrade.py', 'distribution.py'):
                (package / module).write_bytes(Path(u.__file__).with_name(module).read_bytes())
            dist = site / ('paperdrop-' + u.__version__ + '.dist-info')
            dist.mkdir()
            (prefix / 'pipx_metadata.json').write_text(json.dumps({
                'pipx_metadata_version': '0.5', 'main_package': {'package': 'paperdrop', 'suffix': ''}}))
            bindir = root / 'bin'
            bindir.mkdir()
            installer = bindir / 'pipx'
            marker = root / 'installer-calls.json'
            mode = root / 'mode'
            installer.write_text('#!' + sys.executable + '\n' +
                'import json,os,pathlib,sys\n' +
                'root=pathlib.Path(%r)\n' % str(root) +
                'assert os.environ["PIPX_HOME"]==str(root/"pipx")\n' +
                'assert sys.argv[1:3]==["install","--force"]\n' +
                'assert sys.argv[3].startswith("--pip-args=")\n' +
                'assert sys.argv[4].startswith(%r)\n' % (BASE + WHEEL + '#sha256=') +
                'assert "GH_TOKEN" not in os.environ and "HTTPS_PROXY" not in os.environ\n' +
                'assert os.environ["PIP_CONFIG_FILE"]==os.devnull\n' +
                'assert os.environ["NETRC"]==os.devnull\n' +
                'pathlib.Path(%r).write_text(json.dumps(sys.argv[1:]))\n' % str(marker) +
                'mode=pathlib.Path(%r).read_text()\n' % str(mode) +
                'if mode=="failure": sys.exit(7)\n' +
                'if mode=="success":\n' +
                ' pathlib.Path(%r).write_text("__version__ = \'0.3.0\'\\n")\n' % str(package / '__init__.py') +
                ' pathlib.Path(%r).write_text("Metadata-Version: 2.1\\nName: paperdrop\\nVersion: 0.3.0\\n")\n' % str(dist / 'METADATA'))
            installer.chmod(0o700)
            home = root / 'home'
            protected = [home / '.config' / 'paperdrop' / 'config.json',
                         home / '.local' / 'state' / 'paperdrop' / 'state.json',
                         home / '.config' / 'systemd' / 'user' / 'paperdrop.timer',
                         home / 'Library' / 'LaunchAgents' / 'paperdrop.plist']
            for path in protected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'{"source":"keep", "time":"08:35", "paused":true}')
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected}
            temp = root / 'tmp'
            temp.mkdir()
            env = {'PATH': str(bindir) + os.pathsep + os.defpath, 'HOME': str(home),
                   'TMPDIR': str(temp), 'GH_TOKEN': 'SECRET', 'HTTPS_PROXY': 'https://user:SECRET@invalid',
                   'PYTHONDONTWRITEBYTECODE': '1'}
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_GET(self):
                    url = urllib.parse.unquote(self.path[1:])
                    self.server.seen.append((url, self.headers.get('Authorization')))
                    body, content_type = self.server.payloads[url]
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = 'http://127.0.0.1:%d/' % server.server_address[1]
            runner = '''import json,urllib.request,urllib.parse
from unittest.mock import patch
from paperdrop.upgrade import UpgradeError, upgrade_software
transport=urllib.request.build_opener(urllib.request.ProxyHandler({}))
class Forward:
 def open(self, request, timeout):
  local=BASE+urllib.parse.quote(request.full_url, safe='')
  return transport.open(urllib.request.Request(local,headers=request.headers),timeout=timeout)
with patch('urllib.request.build_opener',return_value=Forward()):
 try:
  output={'result':upgrade_software()}
 except UpgradeError as error:
  output={'error':str(error)}
print(json.dumps(output))
'''.replace('BASE', repr(base))
            wheel = b'bounded integration artifact fixture'
            digest = hashlib.sha256(wheel).hexdigest()
            try:
                for scenario in ('success', 'unchanged', 'failure', 'noop', 'tampered'):
                    with self.subTest(scenario=scenario):
                        (package / '__init__.py').write_text('__version__ = "0.2.3"\n')
                        (dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: paperdrop\nVersion: 0.2.3\n')
                        marker.unlink(missing_ok=True)
                        mode.write_text(scenario)
                        releases = [release()] if scenario != 'noop' else []
                        server.seen = []
                        server.payloads = {
                            u.RELEASES_URL: (json.dumps(releases).encode(), 'application/json'),
                            BASE + WHEEL: (wheel if scenario != 'tampered' else b'tampered', 'application/octet-stream'),
                            BASE + 'SHA256SUMS': ((digest + '  ' + WHEEL + '\n').encode(), 'text/plain')}
                        process = subprocess.run([str(python), '-I', '-B', '-c', runner], env=env,
                                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
                        self.assertEqual(process.returncode, 0, process.stderr.decode())
                        outcome = json.loads(process.stdout)
                        if scenario in ('success', 'noop'):
                            self.assertEqual(outcome['result'], {'ok': True, 'changed': scenario == 'success',
                                'version': '0.3.0' if scenario == 'success' else '0.2.3', 'python': str(python)})
                        else:
                            self.assertIn('error', outcome)
                            self.assertNotIn('SECRET', outcome['error'])
                        self.assertEqual(marker.exists(), scenario in ('success', 'unchanged', 'failure'))
                        self.assertEqual(server.seen[0], (u.RELEASES_URL, None))
                        self.assertTrue(all(auth is None for _, auth in server.seen))
                        self.assertEqual(len(server.seen), 1 if scenario == 'noop' else 3)
                        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
