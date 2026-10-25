"""Opt-in upgrades from the fixed official release repository.

``upgrade_software()`` returns ``ok``, ``changed``, ``version`` and ``python``.
Failures raise ``UpgradeError`` with a credential-free message. No UI, wallpaper,
configuration, scheduler, or service is read or changed by this module.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import http.client

from . import __version__


class UpgradeError(RuntimeError):
    """Safe to display to the CLI; never contains remote exception text."""


__all__ = ['UpgradeError', 'upgrade_software']


_VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', re.ASCII)


def _version(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value) or len(value) > 64:
        raise UpgradeError('Invalid software version.')
    return tuple(int(part) for part in value.split('.'))


def _select_release(releases, current):
    current_key = _version(current)
    if not isinstance(releases, list):
        raise UpgradeError('Invalid release list.')
    candidates = []
    for release in releases:
        if not isinstance(release, dict):
            raise UpgradeError('Invalid release entry.')
        if type(release.get('draft')) is not bool or type(release.get('prerelease')) is not bool:
            raise UpgradeError('Invalid release flags.')
        tag = release.get('tag_name')
        if not isinstance(tag, str):
            raise UpgradeError('Invalid release tag.')
        if release['draft'] or not tag.startswith('v'):
            continue
        try:
            key = _version(tag[1:])
        except UpgradeError:
            continue
        if key > current_key:
            candidates.append((key, release))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


RELEASES_URL = 'https://api.github.com/repos/PelikanM400/paperdrop/releases?per_page=100'
_DOWNLOAD_BASE = 'https://github.com/PelikanM400/paperdrop/releases/download/'


def _release_assets(release):
    tag = release['tag_name']
    version = tag[1:]
    _version(version)
    wheel = 'paperdrop-%s-py3-none-any.whl' % version
    assets = release.get('assets')
    if not isinstance(assets, list):
        raise UpgradeError('Invalid release assets.')
    names = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise UpgradeError('Invalid release asset.')
        name = asset.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
            raise UpgradeError('Invalid release asset name.')
        if name in names:
            raise UpgradeError('Duplicate release asset name.')
        url = _DOWNLOAD_BASE + tag + '/' + name
        if asset.get('browser_download_url') != url:
            raise UpgradeError('Release asset must use its exact official HTTPS URL.')
        names[name] = url
    if wheel not in names or 'SHA256SUMS' not in names:
        raise UpgradeError('Release wheel or SHA256SUMS is missing.')
    return names[wheel], names['SHA256SUMS'], wheel


def _checksum(data, wheel):
    try:
        text = data.decode('ascii')
    except (UnicodeError, AttributeError):
        raise UpgradeError('Invalid SHA256SUMS encoding.') from None
    hashes = {}
    for line in text.splitlines():
        if not line:
            continue
        match = re.fullmatch(r'([a-fA-F0-9]{64}) [ *]([A-Za-z0-9][A-Za-z0-9._-]*)', line)
        if not match:
            raise UpgradeError('Invalid SHA256SUMS record.')
        digest, name = match.groups()
        if name in hashes:
            raise UpgradeError('Duplicate SHA256SUMS record.')
        hashes[name] = digest.lower()
    if wheel not in hashes:
        raise UpgradeError('Release wheel checksum is missing.')
    return hashes[wheel]


_MANUAL = ('Automatic upgrade requires a canonical, non-editable pipx installation of paperdrop. '
           'Use your original installer to upgrade manually; configuration is unchanged.')


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(root)
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def _installation():
    """Fail closed rather than infer installations from executable names."""
    try:
        prefix = Path(sys.prefix)
        python = prefix / 'bin' / 'python'
        if (not prefix.is_absolute() or prefix.resolve() != prefix
                or prefix == Path(sys.base_prefix).resolve()
                or prefix.name != 'paperdrop' or prefix.parent.name != 'venvs'
                or Path(sys.executable).parent != prefix / 'bin'
                or not python.is_file() or not os.access(str(python), os.X_OK)
                or not _inside(__file__, prefix)):
            raise UpgradeError(_MANUAL)
        path = prefix / 'pipx_metadata.json'
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024
                or info.st_uid != os.geteuid() or info.st_mode & 0o022):
            raise UpgradeError(_MANUAL)
        # O_NOFOLLOW also closes the symlink replacement window after lstat.
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise UpgradeError(_MANUAL)
        record = json.loads(data)
        if not isinstance(record, dict):
            raise UpgradeError(_MANUAL)
        main = record.get('main_package')
        if (not isinstance(main, dict) or main.get('package') != 'paperdrop'
                or main.get('suffix', '') != ''
                or record.get('pipx_metadata_version') not in {
                    '0.1', '0.2', '0.3', '0.4', '0.5', '0.6',
                    '0.7', '0.8', '0.9', '0.10', '0.11', '0.12'}
                or record.get('backend', 'pip') not in {'pip', 'uv'}
                or record.get('pinned', False) is not False
                or main.get('pinned', False) is not False):
            raise UpgradeError(_MANUAL)
        dist = metadata.distribution('paperdrop')
        if dist.version != __version__ or not _inside(dist.locate_file(''), prefix):
            raise UpgradeError(_MANUAL)
        direct_url = dist.read_text('direct_url.json')
        if direct_url is not None:
            source = json.loads(direct_url)
            if not isinstance(source, dict):
                raise UpgradeError(_MANUAL)
            directory = source.get('dir_info', {})
            if not isinstance(directory, dict) or directory.get('editable', False) is not False:
                raise UpgradeError(_MANUAL)
        pipx = shutil.which('pipx')
        if not pipx or not Path(pipx).is_absolute() or not os.access(pipx, os.X_OK):
            raise UpgradeError(_MANUAL)
        return {'python': str(python), 'pipx': pipx, 'prefix': str(prefix),
                'home': str(prefix.parent.parent)}
    except (OSError, ValueError, TypeError, AttributeError, metadata.PackageNotFoundError):
        raise UpgradeError(_MANUAL) from None


_ASSET_URL = re.compile(re.escape(_DOWNLOAD_BASE)
                        + r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)/'
                        + r'[A-Za-z0-9][A-Za-z0-9._-]*', re.ASCII)
NETWORK_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 120
MAX_RELEASE_BYTES = 2 * 1024 * 1024
MAX_SUMS_BYTES = 128 * 1024
MAX_WHEEL_BYTES = 32 * 1024 * 1024


class _ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    """Only GitHub's credential-free HTTPS release CDN may receive redirects."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            target = urllib.parse.urlsplit(newurl)
            if (req.full_url == RELEASES_URL or target.scheme != 'https'
                    or target.netloc not in {'release-assets.githubusercontent.com',
                                             'objects.githubusercontent.com'}
                    or target.fragment or any(ord(c) < 33 or ord(c) == 127 for c in newurl)):
                raise UpgradeError('Unsafe release redirect rejected.')
        except (ValueError, TypeError):
            raise UpgradeError('Unsafe release redirect rejected.') from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url, limit, kind):
    if not isinstance(url, str) or (url != RELEASES_URL and not _ASSET_URL.fullmatch(url)):
        raise UpgradeError('Only fixed official release HTTPS URLs are permitted.')
    accepted = {'json': {'application/json', 'application/vnd.github+json'},
                'sums': {'text/plain', 'application/octet-stream'},
                'wheel': {'application/octet-stream', 'application/zip'}}
    if kind not in accepted:
        raise UpgradeError('Invalid download type.')
    # Do not inherit proxy passwords, cookies, netrc, tokens, or auth handlers.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _ReleaseRedirect())
    request = urllib.request.Request(url, headers={
        'User-Agent': 'PaperDrop-upgrade/' + __version__,
        'Accept': 'application/vnd.github+json' if kind == 'json' else 'application/octet-stream',
        'Accept-Encoding': 'identity',
    })
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT
    try:
        with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
            if response.status != 200 or response.headers.get_content_type() not in accepted[kind]:
                raise UpgradeError('Unexpected release download response type or status.')
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise UpgradeError('Encoded release download rejected.')
            header = response.headers.get('Content-Length')
            expected = None
            if header is not None:
                if not re.fullmatch(r'[0-9]+', header) or len(header) > 12:
                    raise UpgradeError('Invalid release download size.')
                expected = int(header)
                if expected > limit:
                    raise UpgradeError('Release download exceeds the byte limit.')
            data = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise UpgradeError('Release download timed out.')
                chunk = response.read(min(64 * 1024, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > limit:
                    raise UpgradeError('Release download exceeds the byte limit.')
            if expected is not None and len(data) != expected:
                raise UpgradeError('Truncated release download.')
            return bytes(data)
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        raise UpgradeError('Release download failed; check network access and retry.') from None


def _run_command(argv, env, timeout, capture=False):
    """Run without a shell; discard installer logs and cap probe output."""
    process = None
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=env, cwd='/',
                                   shell=False, start_new_session=True)
        data = bytearray()
        if capture:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise UpgradeError('Software upgrade command timed out; inspect the installation manually.')
                    chunk = os.read(process.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > 4096:
                        raise UpgradeError('Installed-version output exceeds the byte limit.')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UpgradeError('Software upgrade command timed out; inspect the installation manually.')
        if process.wait(timeout=remaining) != 0:
            raise UpgradeError('Software upgrade command failed; repair using your original installer.')
        return bytes(data)
    except (OSError, subprocess.SubprocessError):
        raise UpgradeError('Software upgrade command failed or timed out; inspect the installation manually.') from None
    finally:
        if process is not None:
            # Kill the entire private process group on failure/timeout, including
            # pip children; never leave a detached installer changing files.
            if sys.exc_info()[0] is not None:
                for stop in (lambda: os.killpg(process.pid, signal.SIGKILL), process.kill):
                    try:
                        stop()
                        break
                    except (OSError, AttributeError):
                        continue
            if process.stdout is not None:
                process.stdout.close()
            process.wait()


def _command_env(installation):
    env = {name: os.environ[name] for name in ('HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL',
                                               'TMPDIR', 'PIPX_BIN_DIR', 'PIPX_MAN_DIR')
           if name in os.environ}
    env.update({'PATH': os.defpath, 'PIPX_HOME': installation['home'],
                'PIP_CONFIG_FILE': os.devnull, 'NETRC': os.devnull,
                'PIP_NO_INDEX': '1', 'PIP_NO_INPUT': '1',
                'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PIP_DEFAULT_TIMEOUT': '15',
                'PIPX_DEFAULT_BACKEND': 'pip', 'PIPX_USE_UV': '0',
                'PYTHONDONTWRITEBYTECODE': '1'})
    return env


_PROBE = (
    'import sys,json,paperdrop; from importlib.metadata import version; '
    'print(json.dumps({"version":version("paperdrop"),'
    '"module_version":paperdrop.__version__,"prefix":sys.prefix,"module":paperdrop.__file__}))'
)


def upgrade_software():
    """Upgrade only canonical pipx PaperDrop, or raise a display-safe error.

    No arguments are accepted: the endpoint, repository and running version are
    fixed. Return ``{'ok': True, 'changed': bool, 'version': str, 'python': str}``.
    ``python`` is the installation's bin/python (not its resolved base Python).
    A failed install may need repair with the original installer; no rollback or
    UI launch is attempted. User configuration and services are never touched.
    """
    installation = _installation()
    data = _download(RELEASES_URL, MAX_RELEASE_BYTES, 'json')
    try:
        releases = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise UpgradeError('Invalid release JSON.') from None
    selected = _select_release(releases, __version__)
    result = {'ok': True, 'changed': False, 'version': __version__,
              'python': installation['python']}
    if selected is None:
        return result
    wheel_url, sums_url, wheel = _release_assets(selected)
    digest = _checksum(_download(sums_url, MAX_SUMS_BYTES, 'sums'), wheel)
    artifact = _download(wheel_url, MAX_WHEEL_BYTES, 'wheel')
    if hashlib.sha256(artifact).hexdigest() != digest:
        raise UpgradeError('Release wheel SHA256 verification failed; nothing was installed.')
    del artifact
    env = _command_env(installation)
    # Keep a durable official URL in pipx metadata, never a deleted temp wheel.
    # No dependencies or package index: preserve the already installed Pillow.
    command = [installation['pipx'], 'install', '--force',
               '--pip-args=--no-deps --no-index --disable-pip-version-check --no-input --no-cache-dir',
               wheel_url + '#sha256=' + digest]
    _run_command(command, env, timeout=300)
    output = _run_command([installation['python'], '-I', '-B', '-c', _PROBE],
                          env, timeout=20, capture=True)
    version = selected['tag_name'][1:]
    try:
        confirmed = json.loads(output)
    except (ValueError, UnicodeError, RecursionError):
        raise UpgradeError('Installed version could not be confirmed; repair using your original installer.') from None
    if (not isinstance(confirmed, dict) or confirmed.get('version') != version
            or confirmed.get('module_version') != version
            or confirmed.get('prefix') != installation['prefix']
            or not isinstance(confirmed.get('module'), str)
            or not _inside(confirmed['module'], Path(installation['prefix']))):
        raise UpgradeError('Installed version did not match the release; repair using your original installer.')
    result.update(changed=True, version=version)
    return result
