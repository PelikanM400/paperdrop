"""Small shared helpers: paths, atomic file writes, hashing, URL checks."""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import secrets
import urllib.parse

HEX64 = re.compile(r'^[0-9a-f]{64}$')


def expand_path(value):
    """Expand ~ and environment markers; return an absolute Path (not resolved)."""
    text = os.path.expandvars(os.path.expanduser(str(value)))
    return pathlib.Path(text)


def ensure_dir(path):
    path = pathlib.Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path, data, mode=0o644):
    """Write bytes to `path` via a same-directory temp file + os.replace."""
    path = pathlib.Path(path)
    ensure_dir(path.parent)
    temp = path.parent / ('.' + path.name + '.tmp-' + secrets.token_hex(6))
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    try:
        os.replace(str(temp), str(path))
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return path


def read_json(path, default=None):
    path = pathlib.Path(path)
    if not path.is_file():
        return default
    import json
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    import json
    return atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def is_hex64(value):
    return isinstance(value, str) and bool(HEX64.match(value))


def strip_url_credentials(url):
    """Return (safe_url, had_credentials). Credentials must never be used."""
    parts = urllib.parse.urlsplit(url)
    had = bool(parts.username or parts.password)
    if not had:
        return url, False
    host = parts.hostname or ''
    if parts.port:
        host = host + ':' + str(parts.port)
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment)), True


REPO_PAGE_PATTERNS = (
    re.compile(r'^/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$'),
    re.compile(r'^/(?P<owner>[^/]+)/(?P<repo>[^/]+?)/(tree|blob|commits|releases|issues|pulls)(/|$)'),
)


def looks_like_repository_page(url):
    """True when the URL is a repository or directory page rather than a file.

    A repository home page is not a wallpaper source: it is HTML, not an image
    and not a manifest.
    """
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or '').lower()
    path = parts.path or '/'
    if host in ('github.com', 'www.github.com', 'gitee.com', 'www.gitee.com'):
        for pattern in REPO_PAGE_PATTERNS:
            if pattern.match(path):
                return True
    if host.endswith('githubusercontent.com') or host.endswith('giteeusercontent.com'):
        return False
    return path.endswith('/')
