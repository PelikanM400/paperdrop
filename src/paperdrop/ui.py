"""Local settings page for PaperDrop (``paperdrop ui``).

A small, dependency-free HTTP server bound to 127.0.0.1 that edits the *same*
``config.json`` the command line uses, and drives the *same* commands through
``python -m paperdrop``. The CLI's behaviour is unchanged: this module adds a page,
it does not reimplement the pipeline.

Safety rules:
* loopback only, random per-run token in the URL, Host header check (DNS rebinding),
  JSON-only POSTs with an Origin/Sec-Fetch-Site check (no cross-site writes);
* every write is validated first, the old file is backed up, the new one is written
  atomically, then re-read and compared;
* a schedule change re-runs ``install`` (always with ``--no-ui``); if that fails the
  config is rolled back.

First run: with no config file yet, the page offers the example config plus a preset
source *in memory only*. Nothing is written, fetched or scheduled until the user checks
and confirms; the first apply must validate a manifest, creates the config 0600 without
ever overwriting an existing entry, installs the schedule, and removes both again if the
install fails.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import http.server
import json
import os
import pathlib
import secrets
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import webbrowser

from . import __version__
from .config import example_config, load_config, validate
from .paths import Paths, default_config_file
from .sources import _reject_repository_page, parse_manifest

PRESETS = [
    ('作者的 Gitee 横向壁纸库', 'https://gitee.com/PelikanM400/pelikan_daily_wallpaper/raw/master/desk.json',
     "Author's Gitee landscape wallpaper library"),
    ('作者的 Gitee 纵向壁纸库', 'https://gitee.com/PelikanM400/pelikan_daily_wallpaper/raw/master/current.json',
     "Author's Gitee portrait wallpaper library"),
    ('Bing 每日壁纸', 'https://gitee.com/PelikanM400/pelikan_daily_wallpaper/raw/master/bing.json',
     'Bing daily wallpaper'),
    ('作者的 GitHub 壁纸库', 'https://raw.githubusercontent.com/PelikanM400/pelikan_daily_wallpaper/main/desk.json',
     "Author's GitHub wallpaper library"),
]

MAX_BODY = 64 * 1024
MAX_FETCH = 64 * 1024 * 1024
MAX_LOG_LINES = 400
FETCH_DEADLINE_SECONDS = 20
CONFIG_MODE = 0o600
PAGE_OVERRIDE_ENV = 'PAPERDROP_UI_PAGE'
PAGE_OVERRIDE_MAX = 2 * 1024 * 1024

# One writer at a time: two concurrent applies must not interleave read/write/rollback.
_WRITE_LOCK = threading.Lock()


def _redirect_allowed(target, allow_http):
    scheme = urllib.parse.urlsplit(target).scheme.lower()
    return scheme in (('http', 'https') if allow_http else ('https',))


def _connect(url, timeout):
    """Open, connect and send one GET; the caller keeps the connection to close it."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('not an http(s) URL')
    path = parts.path or '/'
    if parts.query:
        path += '?' + parts.query
    factory = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
    connection = factory(parts.hostname, parts.port, timeout=timeout)
    connection.connect()  # bounded by the socket timeout, so it cannot leak forever
    connection.request('GET', path, headers={'User-Agent': 'PaperDrop/' + __version__,
                                             'Accept': '*/*'})
    return connection


def _quiet_close(connection):
    sock = getattr(connection, 'sock', None)
    try:
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)  # wakes a blocked recv immediately
    except OSError:
        pass
    try:
        if connection is not None:
            connection.close()
    except Exception:
        pass

PAGE = (pathlib.Path(__file__).with_name('assets') / 'ui.html').read_text(encoding='utf-8')
FONT_PATH = pathlib.Path(__file__).with_name('assets') / 'fonts' / 'source-han-sans-cn.woff2'


def _run_cli(config_path, *args, timeout=300):
    """Drive the unchanged CLI in a child process and capture what it said."""
    command = [sys.executable, '-m', 'paperdrop', '--config', str(config_path), *args]
    if args and args[0] == 'install' and '--no-ui' not in args:
        command.append('--no-ui')  # internal scheduling must never reopen onboarding
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'exit_code': None, 'output': 'timed out after %ss' % timeout}
    except OSError as error:
        return {'ok': False, 'exit_code': None, 'output': str(error)}
    output = ((done.stdout or '') + (done.stderr or '')).strip()
    return {'ok': done.returncode == 0, 'exit_code': done.returncode, 'output': output[-4000:]}


def _read_state(paths):
    try:
        return json.loads(paths.state_file.read_text())
    except (OSError, ValueError):
        return {}


def page_html(token=None):
    """Return the packaged page, or a validated development override."""
    presets = json.dumps(PRESETS, ensure_ascii=False)
    html = PAGE
    override = os.environ.get(PAGE_OVERRIDE_ENV)
    if override:
        try:
            path = pathlib.Path(override).expanduser()
            if path.is_file() and path.stat().st_size <= PAGE_OVERRIDE_MAX:
                html = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            pass
    active_token = token or getattr(Handler, 'token', '') or ''
    font_url = '/assets/fonts/source-han-sans-cn.woff2?t=' + urllib.parse.quote(active_token, safe='')
    return (html.replace('__PRESETS__', presets)
            .replace('__PAPERDROP_FONT_URL__', font_url).encode('utf-8'))


def _schedule_state(config):
    """Best effort, and honest about it: never claim a state we did not observe."""
    try:
        from .platforms import detect_platform
        platform = detect_platform(config)
    except Exception:
        return '未知'
    getter = getattr(platform, 'timer_state', None)
    if getter is None:
        return '未知（该平台不支持查询）'
    try:
        state = getter()
    except Exception:
        return '未知'
    return {'enabled': '已启用', 'active': '已启用', 'running': '已启用',
            'disabled': '已停止', 'inactive': '已停止', 'paused': '已暂停'}.get(str(state), str(state))


def _ui_config(config_path):
    """Read the existing raw config, or offer a preset only in memory.

    lstat deliberately distinguishes an absent path from dangling symlinks and
    other occupied targets. Never interpret an unreadable/invalid file as new.
    """
    path = pathlib.Path(config_path)
    try:
        path.lstat()
    except FileNotFoundError:
        raw = example_config()
        raw['source']['url'] = PRESETS[0][1]
        return raw, path, True
    load_config(path)  # fail closed on invalid existing configuration
    return json.loads(path.read_text(encoding='utf-8')), path, False


def status_payload(config_path):
    raw, resolved, onboarding = _ui_config(config_path)
    config = validate(raw)
    paths = Paths.from_config(config, config_file=resolved)
    schedule, source = config['schedule'], config['source']
    platform = 'macOS' if sys.platform == 'darwin' else ('linux' if sys.platform.startswith('linux') else sys.platform)
    return {
        'version': __version__,
        'config_path': str(resolved),
        'source': {'mode': source['mode'], 'url': source['url'],
                   'min_width': source['min_width'], 'min_height': source['min_height']},
        'schedule': {'daily_time': schedule['daily_time'],
                     'catch_up_interval_minutes': schedule['catch_up_interval_minutes'],
                     'screen_poll_seconds': schedule['screen_poll_seconds']},
        'onboarding': onboarding,
        'schedule_state': '尚未配置' if onboarding else _schedule_state(config),
        'state': {} if onboarding else _read_state(paths),
        'platform': platform,
        'log_file': str(paths.log_file),
    }


def _fetch_source(source_url, allow_http, budget=None):
    """Fetch with a wall-clock budget covering connect, headers, redirects and body.

    A per-socket timeout is not enough: a server that trickles one byte just under the
    timeout never trips it, and both the status line and the body would wait forever. The
    fetch therefore runs in a worker thread, the caller stops waiting at the budget, and
    the connection (held as soon as it exists) is shut down -- which also ends the worker.
    """
    if budget is None:
        budget = FETCH_DEADLINE_SECONDS
    holder = {}

    def fetch():
        try:
            url = source_url
            for _ in range(6):
                connection = None
                response = None
                try:
                    connection = _connect(url, min(15, budget))
                    holder['connection'] = connection
                    response = connection.getresponse()
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.getheader('Location')
                        if not location:
                            raise ValueError('redirect without a Location header')
                        target = urllib.parse.urljoin(url, location)
                        if not _redirect_allowed(target, allow_http):
                            raise ValueError('redirect to a non-http(s) scheme refused')
                        url = target
                        continue
                    blocks, total = [], 0
                    while True:
                        block = response.read1(256 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > MAX_FETCH:
                            raise ValueError('source is larger than %d bytes' % MAX_FETCH)
                        blocks.append(block)
                    holder['payload'] = b''.join(blocks)
                    holder['content_type'] = response.headers.get('Content-Type', '')
                    holder['status'] = response.status
                    return
                finally:
                    if response is not None:
                        try:
                            response.close()
                        except Exception:
                            pass
                    if connection is not None:
                        try:
                            connection.close()
                        except Exception:
                            pass
            raise ValueError('too many redirects')
        except BaseException as error:  # a shutdown after the deadline lands here
            holder['error'] = error

    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(budget)
    if worker.is_alive():
        connection = holder.get('connection')
        try:
            sock = getattr(connection, 'sock', None)
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)  # wakes the blocked read, unblocks the worker
        except OSError:
            pass
        threading.Thread(target=_quiet_close, args=(connection,), daemon=True).start()
        return {'error': TimeoutError('source took longer than %ss' % budget)}
    if 'error' in holder:
        return {'error': holder['error']}
    return holder


def classify_source(source_url, allow_http=False):
    """Fetch a candidate URL and say what it is. Values are plain text, never HTML."""
    scheme = source_url.split(':', 1)[0].lower()
    if scheme not in ('http', 'https'):
        return {'kind': None, 'rows': [], 'url': source_url, 'error': '必须是 http(s) 地址'}
    if scheme == 'http' and not allow_http:
        return {'kind': None, 'rows': [], 'url': source_url,
                'error': '明文 http 被配置拒绝（http.allow_http 为 false），请用 https'}
    if scheme == 'https':
        try:
            _reject_repository_page(source_url)
        except Exception as error:
            return {'kind': None, 'rows': [], 'url': source_url, 'error': str(error)}
    outcome = _fetch_source(source_url, allow_http)
    if 'error' in outcome:
        return {'kind': None, 'rows': [], 'url': source_url,
                'error': '%s: %s' % (type(outcome['error']).__name__, outcome['error'])}
    payload = outcome['payload']
    content_type = outcome['content_type']
    status = outcome['status']
    rows = [['HTTP', str(status)], ['Content-Type', content_type or '—'],
            ['大小', '%d 字节' % len(payload)], ['sha256', hashlib.sha256(payload).hexdigest()[:24] + '…']]
    if 'json' in content_type.lower() or payload[:1] in (b'{', b'['):
        try:
            target = parse_manifest(payload, source_url)
        except Exception as error:
            return {'kind': None, 'rows': rows, 'error': '清单校验失败: %s' % error}
        rows += [['识别为', '清单（manifest）'], ['日期', str(target.date)],
                 ['尺寸', '%s×%s' % (target.width, target.height)],
                 ['图片 URL', target.url]]
        return {'kind': 'manifest', 'rows': rows, 'url': source_url, 'error': None}
    rows += [['识别为', '图片直链（没有哈希校验）']]
    return {'kind': 'image', 'rows': rows, 'url': source_url, 'error': None}


def _validate(candidate):
    """Validate exactly the way the CLI does, via a temporary file."""
    handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
    try:
        json.dump(candidate, handle, ensure_ascii=False)
        handle.close()
        load_config(pathlib.Path(handle.name))
    finally:
        pathlib.Path(handle.name).unlink(missing_ok=True)


def _prepare(config_path, daily_time, source_url, verdict=None):
    """Work out the exact configuration that would be written, without writing it."""
    raw, _, onboarding = _ui_config(config_path)
    current_time = raw.get('schedule', {}).get('daily_time')
    current_source = raw.get('source', {})
    current_url = current_source.get('url')
    allow_http = bool(raw.get('http', {}).get('allow_http'))

    candidate = json.loads(json.dumps(raw))
    candidate.setdefault('schedule', {})['daily_time'] = daily_time
    candidate.setdefault('source', {})['url'] = source_url

    messages, ok = [], True
    if onboarding or source_url != current_url:
        if verdict is None or verdict.get('url') != source_url:
            verdict = classify_source(source_url, allow_http)
        if verdict['error']:
            ok = False
            messages.append({'level': 'error', 'text': verdict['error']})
        elif verdict['kind'] == 'image':
            ok = False
            messages.append({'level': 'error', 'text':
                             '这是图片直链，不是清单。页面只写 manifest 模式；'
                             '要用图片直链，请用命令行把 source.mode 改成 image 并设 '
                             'source.image_mode_acknowledged=true。'})
        else:
            candidate['source']['mode'] = 'manifest'
    try:
        if onboarding:
            validate(candidate)
        else:
            _validate(candidate)
    except Exception as error:
        ok = False
        messages.append({'level': 'error', 'text': str(error)})
    if daily_time == current_time:
        messages.append({'level': 'info', 'text': '启动时间未变化。'})
    if source_url == current_url:
        messages.append({'level': 'info', 'text': '图源未变化。'})
    changes = []
    if daily_time != current_time:
        changes.append(['schedule.daily_time', current_time or '', daily_time])
    if source_url != current_url:
        changes.append(['source.url', current_url or '', source_url])
    if candidate['source'].get('mode') != current_source.get('mode'):
        changes.append(['source.mode', current_source.get('mode') or '', candidate['source'].get('mode')])
    return {'ok': ok, 'messages': messages, 'changes': changes,
            'onboarding': onboarding,
            'needs_install': onboarding or daily_time != current_time,
            'candidate': candidate if ok else None}


def preview_changes(config_path, daily_time, source_url):
    prepared = _prepare(config_path, daily_time, source_url)
    prepared.pop('candidate', None)
    return prepared


def check_preview(config_path, daily_time, source_url):
    """Combine a one-off source check with a read-only draft preview."""
    config, _, _ = _ui_config(config_path)
    verdict = classify_source(source_url, bool(config.get('http', {}).get('allow_http')))
    prepared = _prepare(config_path, daily_time, source_url, verdict=verdict)
    source_error = verdict.get('error')
    if not source_error and verdict.get('kind') != 'manifest':
        source_error = ('这是图片直链，不是清单。页面只支持 JSON 清单图源；'
                        '如需使用图片直链，请通过命令行设置。')
    source_check = {'ok': source_error is None, 'kind': verdict.get('kind'),
                    'rows': verdict.get('rows', []), 'error': source_error}
    if source_error:
        prepared['ok'] = False
        prepared['candidate'] = None
        if not any(message.get('text') == source_error for message in prepared['messages']):
            prepared['messages'].append({'level': 'error', 'text': source_error})
    prepared.pop('candidate', None)
    prepared['source_check'] = source_check
    return prepared


def _atomic_write(path, text, mode=CONFIG_MODE, create_only=False):
    path = pathlib.Path(path)
    temporary = path.with_name(path.name + '.tmp-' + secrets.token_hex(6))
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    identity = os.fstat(descriptor)
    published = False
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if create_only:
            # link publishes the complete fsynced file atomically, but unlike
            # replace refuses every occupied target (including dangling links).
            os.link(temporary, path)
            published = True
            temporary.unlink()
        else:
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        if published:
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    path.unlink()
            except OSError:
                pass
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _backup(config_path, original_text):
    """Unique, never overwritten, created at 0600 before anything is written."""
    stamp = __import__('datetime').datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')
    for _ in range(64):
        candidate = config_path.with_name('%s.bak-%s-%s' % (config_path.name, stamp, secrets.token_hex(3)))
        try:
            descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, CONFIG_MODE)
        except FileExistsError:
            continue
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(original_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(candidate, CONFIG_MODE)
        return candidate
    raise RuntimeError('could not create a unique backup')


def apply_changes(config_path, daily_time, source_url, create_only=False):
    """Serialised: validate, back up, write atomically, re-read, reinstall, else restore."""
    config_path = pathlib.Path(config_path)
    # Classify before taking the write lock: a slow source must not block other writers.
    with _WRITE_LOCK:
        if create_only and os.path.lexists(config_path):
            raise ValueError('configuration already exists; refresh settings before applying')
        current, _, onboarding = _ui_config(config_path)
    verdict = None
    if onboarding or source_url != current.get('source', {}).get('url'):
        verdict = classify_source(source_url, bool(current.get('http', {}).get('allow_http')))
    with _WRITE_LOCK:
        prepared = _prepare(config_path, daily_time, source_url, verdict=verdict)
        if not prepared['ok']:
            raise ValueError('; '.join(m['text'] for m in prepared['messages'] if m['level'] == 'error'))
        if onboarding:
            config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                _atomic_write(config_path, json.dumps(prepared['candidate'], ensure_ascii=False, indent=2) + '\n',
                              create_only=True)
            except FileExistsError:
                raise ValueError('configuration already exists; refresh settings before applying') from None
            try:
                reloaded, _ = load_config(config_path)
                if (reloaded['schedule']['daily_time'] != daily_time
                        or reloaded['source']['url'] != source_url):
                    raise ValueError('written configuration did not read back as expected')
            except Exception:
                config_path.unlink(missing_ok=True)
                raise
            try:
                install = _run_cli(config_path, 'install')
            except Exception as error:
                install = {'ok': False, 'exit_code': None, 'output': str(error)}
            if install['ok']:
                return {'ok': True, 'backup': None, 'install': install,
                        'changes': prepared['changes']}
            # The CLI needs the candidate config to remove a partial schedule.
            # Never reinstall defaults here: there was no previous installation.
            try:
                restore = _run_cli(config_path, 'uninstall')
            except Exception as error:
                restore = {'ok': False, 'exit_code': None, 'output': str(error)}
            try:
                config_path.unlink()
                removed = True
            except OSError:
                removed = False
            if not removed:
                note = ('install failed and the new configuration could not be removed '
                        'from %s; delete it manually' % config_path)
            elif restore['ok']:
                note = 'install failed; new configuration and schedule rolled back'
            else:
                note = ('install failed; new configuration removed, but removing the new '
                        'schedule also failed — schedule cleanup is incomplete; run '
                        '"paperdrop uninstall" manually with a valid configuration')
            said = ' '.join(str(install.get('output') or '').split())[:400]
            if said:
                note += '; installer said: ' + said
            return {'ok': False, 'backup': None, 'install': install, 'restore': restore,
                    'changes': prepared['changes'], 'error': note}
        if not prepared['changes']:
            return {'ok': True, 'backup': None, 'install': None, 'changes': []}

        original_text = config_path.read_text(encoding='utf-8')
        backup = _backup(config_path, original_text)
        _atomic_write(config_path, json.dumps(prepared['candidate'], ensure_ascii=False, indent=2) + '\n')
        try:
            reloaded, _ = load_config(config_path)
            if (reloaded['schedule']['daily_time'] != daily_time
                    or reloaded['source']['url'] != source_url):
                raise ValueError('written configuration did not read back as expected')
        except Exception:
            _atomic_write(config_path, original_text)
            raise

        if not prepared['needs_install']:
            return {'ok': True, 'backup': str(backup), 'install': None,
                    'changes': prepared['changes']}

        install = _run_cli(config_path, 'install')
        if install['ok']:
            return {'ok': True, 'backup': str(backup), 'install': install,
                    'changes': prepared['changes']}

        # Roll back both sides: the config *and* the schedule the failed install rewrote.
        _atomic_write(config_path, original_text)
        restore = _run_cli(config_path, 'install')
        note = ('install failed; configuration rolled back, previous schedule reinstalled'
                if restore['ok'] else
                'install failed; configuration rolled back, but reinstalling the previous '
                'schedule also failed — run "paperdrop install" manually')
        return {'ok': False, 'backup': str(backup), 'install': install, 'restore': restore,
                'changes': prepared['changes'], 'error': note}


def log_tail(paths, lines):
    lines = max(1, min(int(lines), MAX_LOG_LINES))
    try:
        text = paths.log_file.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''
    return '\n'.join(text.splitlines()[-lines:])


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'PaperDropUI'
    token: str | None = None
    config_path = pathlib.Path('.')

    def _host_ok(self):
        raw = (self.headers.get('Host') or '').strip()
        if raw.startswith('[') and ']' in raw:
            host = raw[1:raw.find(']')]
        else:
            host = raw.split(':', 1)[0]
        return host in ('127.0.0.1', 'localhost', '::1')

    def _token_ok(self, query):
        if not self.token:
            return False
        presented = query.get('t', [''])[0]
        try:
            return hmac.compare_digest(presented.encode('utf-8'), self.token.encode('utf-8'))
        except (UnicodeError, TypeError):
            return False

    def _same_site(self):
        site = (self.headers.get('Sec-Fetch-Site') or '').lower()
        if site and site not in ('same-origin', 'none'):
            return False
        origin = self.headers.get('Origin')
        if origin:
            parsed = urllib.parse.urlparse(origin)
            return (parsed.hostname or '') in ('127.0.0.1', 'localhost', '::1')
        return True

    def _send(self, status, body, content_type='application/json; charset=utf-8'):
        payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if content_type.startswith('text/html'):
            self.send_header('Content-Security-Policy',
                             "default-src 'none'; style-src 'unsafe-inline'; "
                             "script-src 'unsafe-inline'; connect-src 'self'; font-src 'self'; "
                             "img-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
                             "form-action 'none'")
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def _guard(self, query, post=False):
        if not self._host_ok():
            self._send(403, {'error': 'bad host'})
            return False
        if not self._token_ok(query):
            self._send(403, {'error': 'bad token'})
            return False
        if post:
            if 'application/json' not in (self.headers.get('Content-Type') or '').lower():
                self._send(415, {'error': 'expected application/json'})
                return False
            if not self._same_site():
                self._send(403, {'error': 'cross-site request refused'})
                return False
        return True

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self._guard(query):
            return
        try:
            if parsed.path == '/':
                return self._send(200, page_html(self.token), 'text/html; charset=utf-8')
            if parsed.path == '/assets/fonts/source-han-sans-cn.woff2':
                return self._send(200, FONT_PATH.read_bytes(), 'font/woff2')
            if parsed.path == '/api/status':
                return self._send(200, status_payload(self.config_path))
            if parsed.path == '/api/log':
                if _ui_config(self.config_path)[2]:
                    return self._send(200, {'text': '', 'empty': True})
                config, resolved = load_config(self.config_path)
                paths = Paths.from_config(config, config_file=resolved)
                try:
                    lines = int(query.get('lines', ['200'])[0])
                except (TypeError, ValueError):
                    return self._send(400, {'error': 'lines must be an integer'})
                return self._send(200, {'text': log_tail(paths, lines)})
        except Exception as error:
            return self._send(500, {'error': str(error)})
        return self._send(404, {'error': 'not found'})

    do_HEAD = do_GET

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self._guard(query, post=True):
            return
        try:
            length = min(int(self.headers.get('Content-Length') or 0), MAX_BODY)
            body = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, TypeError) as error:
            return self._send(400, {'error': 'bad json: %s' % error})
        try:
            if parsed.path in ('/api/run', '/api/schedule'):
                _, _, onboarding = _ui_config(self.config_path)
                if onboarding:
                    return self._send(400, {'error': '请先检查并确认应用首次配置，再使用快捷操作。'})
            if parsed.path == '/api/check-preview':
                return self._send(200, check_preview(self.config_path, str(body.get('daily_time') or ''),
                                                    str(body.get('source_url') or '')))
            if parsed.path == '/api/preview':
                return self._send(200, preview_changes(self.config_path, str(body.get('daily_time') or ''),
                                                       str(body.get('source_url') or '')))
            if parsed.path == '/api/check':
                config, _ = load_config(self.config_path)
                url = str(body.get('source_url') or '')
                verdict = classify_source(url, bool(config.get('http', {}).get('allow_http')))
                return self._send(200, {'ok': verdict['error'] is None, 'error': verdict['error'],
                                        'kind': verdict['kind'], 'rows': verdict['rows']})
            if parsed.path == '/api/apply':
                result = apply_changes(self.config_path, str(body.get('daily_time') or ''),
                                       str(body.get('source_url') or ''),
                                       create_only=body.get('onboarding') is True)
                return self._send(200 if result['ok'] else 500, result)
            if parsed.path == '/api/run':
                args = ['run'] + (['--force'] if body.get('force') else [])
                outcome = _run_cli(self.config_path, *args, timeout=240)
                return self._send(200, {'ok': outcome['ok'], 'output': outcome['output'],
                                        'summary': '已执行' if outcome['ok'] else '执行失败（退出码 %s）' % outcome['exit_code']})
            if parsed.path == '/api/schedule':
                action = str(body.get('action') or '')
                if action not in ('pause', 'resume'):
                    return self._send(400, {'error': 'action must be pause or resume'})
                outcome = _run_cli(self.config_path, action, timeout=120)
                return self._send(200, {'ok': outcome['ok'], 'output': outcome['output'],
                                        'summary': ('已暂停' if action == 'pause' else '已恢复') if outcome['ok'] else '操作失败'})
        except ValueError as error:
            return self._send(400, {'error': str(error)})
        except Exception as error:
            return self._send(500, {'error': str(error)})
        return self._send(404, {'error': 'not found'})

    def log_message(self, format, *args):
        # The request line carries ?t=<token>; never let the token reach a terminal log.
        message = format % args
        if self.token:
            message = message.replace(self.token, '***')
        sys.stderr.write('paperdrop ui: ' + message + '\n')


def _serve_path(config_path):
    """Absolute path; resolve the final component only for an existing, readable config.

    A dangling symlink must stay occupied (it is not a fresh install), while a
    symlink to a real config keeps the old behaviour of editing the target file.
    """
    raw = pathlib.Path(os.path.abspath(pathlib.Path(config_path).expanduser()))
    try:
        raw.lstat()
    except OSError:
        return raw
    try:
        raw.stat()
    except OSError:
        return raw
    return raw.resolve()


def serve(config_path=None, port=8765, open_browser=True):
    config_path = _serve_path(config_path) if config_path else default_config_file()
    _, _, onboarding = _ui_config(config_path)  # fail fast on invalid/occupied targets
    token = secrets.token_urlsafe(16)
    Handler.token = token
    Handler.config_path = config_path
    try:
        server = socketserver.ThreadingTCPServer(('127.0.0.1', port), Handler)
    except OSError as error:
        raise SystemExit('paperdrop: cannot bind 127.0.0.1:%d — %s' % (port, error))
    server.daemon_threads = True
    url = 'http://127.0.0.1:%d/?t=%s' % (server.server_address[1], token)
    print('PaperDrop settings (local only)')
    print('config: %s' % config_path)
    if onboarding:
        print('尚未配置：检查并确认应用后才会写入配置并启用定时更换。')
        print('Not configured yet: nothing is written or scheduled until you check and '
              'confirm the settings here.')
    print('open:   %s' % url)
    if os.environ.get(PAGE_OVERRIDE_ENV):
        print('page:   %s (override)' % os.environ[PAGE_OVERRIDE_ENV])
    print('关闭网页不会停止 PaperDrop；按本终端的 Ctrl+C 退出。')
    print('Closing the browser does not stop PaperDrop; press Ctrl+C in this terminal to exit.')
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('')
    finally:
        server.server_close()
    return 0
