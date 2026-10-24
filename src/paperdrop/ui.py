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
* a schedule change re-runs ``install``; if that fails the config is rolled back.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.server
import json
import os
import pathlib
import secrets
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from . import __version__
from .config import load_config
from .paths import Paths, default_config_file
from .sources import _reject_repository_page, parse_manifest

PRESETS = [
    ('Gitee 横版（电脑）', 'https://gitee.com/PelikanM400/pelikan_daily_wallpaper/raw/master/desk.json'),
    ('Gitee 竖版（手机）', 'https://gitee.com/PelikanM400/pelikan_daily_wallpaper/raw/master/current.json'),
    ('Bing 4K（中转清单）', 'https://gitee.com/PelikanM400/pelikan_daily_wallpaper/raw/master/bing.json'),
    ('GitHub 备用（横版）', 'https://raw.githubusercontent.com/PelikanM400/pelikan_daily_wallpaper/main/desk.json'),
]

MAX_BODY = 64 * 1024
MAX_FETCH = 64 * 1024 * 1024
MAX_LOG_LINES = 400
FETCH_DEADLINE_SECONDS = 20
CONFIG_MODE = 0o600

# One writer at a time: two concurrent applies must not interleave read/write/rollback.
_WRITE_LOCK = threading.Lock()


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow http(s) redirects only, like the CLI's Fetcher, and keep a small cap."""
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() not in ('http', 'https'):
            raise urllib.error.HTTPError(newurl, code, 'redirect to a non-http scheme refused',
                                         headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PaperDrop 设置</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans CJK SC",sans-serif;background:#0f1115;color:#e6e8ee}
.wrap{max-width:940px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#8b93a7;font-size:13px}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:22px}
.card{background:#171a21;border:1px solid #242a35;border-radius:12px;padding:18px 20px;margin-bottom:16px}
.card h2{font-size:14px;margin:0 0 14px;color:#aab2c5;font-weight:600}
.row{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-end}
.field{flex:1 1 260px}
label{display:block;font-size:12px;color:#8b93a7;margin-bottom:6px}
input[type=time],input[type=url]{width:100%;padding:9px 11px;border-radius:8px;border:1px solid #2c3442;background:#0d1015;color:#e6e8ee;font-size:14px}
input:focus{outline:2px solid #3b6cf6;outline-offset:1px;border-color:transparent}
button{cursor:pointer;border:1px solid #2c3442;background:#1d2330;color:#dfe4ee;padding:8px 13px;border-radius:8px;font-size:13px;white-space:nowrap}
button:hover:not(:disabled){border-color:#3b6cf6}
button.primary{background:#2f5cf5;border-color:#2f5cf5;color:#fff;font-weight:600}
button.danger{color:#ff8b8b}
button:disabled{opacity:.5;cursor:default}
.presets{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.presets button{font-size:12px;padding:6px 10px;color:#aab2c5}
.kv{display:grid;grid-template-columns:140px 1fr;gap:6px 14px;font-size:13.5px;word-break:break-word}
.kv div:nth-child(odd){color:#8b93a7}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;word-break:break-all}
pre{margin:0;background:#0d1015;border:1px solid #222833;border-radius:8px;padding:12px;overflow:auto;font-size:12px;max-height:260px;white-space:pre-wrap}
.ok{color:#3fcf8e}.bad{color:#ff6b6b}.warn{color:#f5b942}.dim{color:#8b93a7}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #222833;vertical-align:top}
th{color:#8b93a7;font-weight:600}
.hidden{display:none}
.note{font-size:12.5px;color:#8b93a7;margin-top:10px}
</style></head><body><div class="wrap">
<div class="head"><div><h1>PaperDrop 设置</h1><div class="sub" id="sub">仅本机访问 · 改动会写入 config.json（自动备份）</div></div>
<button id="refresh">刷新</button></div>

<div class="card"><h2>当前状态</h2><div class="kv" id="status">加载中…</div></div>

<div class="card"><h2>修改配置</h2>
  <div class="row">
    <div class="field"><label>每天启动时间（24 小时制）</label><input id="time" type="time" step="60"></div>
    <div class="field"><label>图源 URL（必须是 JSON 清单）</label><input id="url" type="url" spellcheck="false"></div>
  </div>
  <div class="presets" id="presets"></div>
  <div class="row" style="margin-top:16px">
    <button class="primary" id="preview">预览变更</button>
    <button id="check">检查链接</button>
    <button class="danger" id="apply" disabled>应用变更</button>
    <button id="reset">还原</button>
  </div>
  <div class="note" id="applyNote">先预览，通过校验后才能应用。</div>
</div>

<div class="card"><h2>操作</h2>
  <div class="row">
    <button id="run">立即检查并更换壁纸</button>
    <button id="update">强制重新下载当前源</button>
    <button id="pause">暂停定时任务</button>
    <button id="resume">恢复定时任务</button>
    <button id="log">查看日志</button>
  </div>
</div>

<div class="card hidden" id="resultCard"><h2 id="resultTitle">结果</h2><div id="result"></div></div>
<div class="card hidden" id="logCard"><h2>日志（最后 200 行）</h2><pre id="logBody"></pre></div>
</div>
<script>
const TOKEN = new URLSearchParams(location.search).get('t') || '';
let CURRENT = null, PREVIEWED = null, BUSY = false;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function setBusy(v){ BUSY = v; document.querySelectorAll('button').forEach(b => { if (b.id !== 'refresh') b.disabled = v; }); if (!v) $('apply').disabled = !PREVIEWED; }
function invalidate(){ PREVIEWED = null; $('apply').disabled = true; $('applyNote').textContent = '输入已修改，请重新预览。'; }
async function api(path, body){
  const url = path + (path.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(TOKEN);
  const opt = body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {};
  const r = await fetch(url, opt);
  let j;
  try { j = await r.json(); } catch (e) { throw new Error('HTTP ' + r.status); }
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
const kv = (rows) => rows.map(([k,v]) => '<div>' + esc(k) + '</div><div>' + esc(v) + '</div>').join('');
function show(title, html){ $('resultTitle').textContent = title; $('result').innerHTML = html; $('resultCard').classList.remove('hidden'); }
function renderStatus(s){
  CURRENT = s;
  const st = s.state || {}, ok = st.last_success || {}, at = st.last_attempt || {};
  $('status').innerHTML = kv([
    ['版本', s.version],
    ['配置', s.config_path],
    ['图源', s.source.url + (s.source.mode === 'image' ? '（image 模式）' : '')],
    ['每天运行', s.schedule.daily_time + '　补检查 ' + s.schedule.catch_up_interval_minutes + ' 分钟　屏幕轮询 ' + s.schedule.screen_poll_seconds + ' 秒'],
    ['上次成功', (ok.local_date || '—') + (ok.finished_at ? '　' + ok.finished_at : '')],
    ['上次检查', (at.local_date || '—') + (at.detail ? '　' + at.detail : '')],
    ['定时任务', s.schedule_state],
    ['平台', s.platform],
  ]);
  $('time').value = s.schedule.daily_time;
  $('url').value = s.source.url;
  PREVIEWED = null; $('apply').disabled = true; $('applyNote').textContent = '先预览，通过校验后才能应用。';
}
async function loadStatus(){ try { renderStatus(await api('/api/status')); } catch (e) { show('无法读取状态', '<div class="bad">' + esc(e.message) + '</div>'); } }
$('presets').innerHTML = __PRESETS__.map((p, i) => '<button data-i="' + i + '">' + esc(p[0]) + '</button>').join('');
$('presets').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; $('url').value = __PRESETS__[Number(b.dataset.i)][1]; PREVIEWED = null; $('apply').disabled = true; };
$('reset').onclick = () => { if (CURRENT) { $('time').value = CURRENT.schedule.daily_time; $('url').value = CURRENT.source.url; invalidate(); } };
$('time').addEventListener('input', invalidate);
$('url').addEventListener('input', invalidate);
$('refresh').onclick = loadStatus;
$('preview').onclick = async () => {
  const body = {daily_time: $('time').value, source_url: $('url').value.trim()};
  setBusy(true);
  try {
    const r = await api('/api/preview', body);
    let html = '<div style="margin-bottom:10px">校验：' + (r.ok ? '<span class="ok">通过</span>' : '<span class="bad">有问题</span>') + '</div>';
    if (r.messages && r.messages.length) html += '<ul>' + r.messages.map(m => '<li class="' + (m.level === 'error' ? 'bad' : 'dim') + '">' + esc(m.text) + '</li>').join('') + '</ul>';
    if (r.changes && r.changes.length) {
      html += '<table><thead><tr><th>字段</th><th>当前</th><th>改为</th></tr></thead><tbody>' + r.changes.map(c =>
        '<tr><td>' + esc(c[0]) + '</td><td><code>' + esc(c[1]) + '</code></td><td><code>' + esc(c[2]) + '</code></td></tr>').join('') + '</tbody></table>';
    } else { html += '<div class="dim">没有字段会变化。</div>'; }
    if (r.needs_install) html += '<div class="warn" style="margin-top:10px">时间变化后会自动重装定时任务。</div>';
    show('预览（未写入）', html);
    PREVIEWED = r.ok && r.changes.length ? body : null;
    $('apply').disabled = !PREVIEWED;
    $('applyNote').textContent = PREVIEWED ? '确认无误后点"应用变更"：会备份旧配置并原子写入。' : '先预览，通过校验后才能应用。';
  } catch (e) { show('预览失败', '<div class="bad">' + esc(e.message) + '</div>'); }
  finally { setBusy(false); }
};
$('check').onclick = async () => {
  setBusy(true);
  try {
    const r = await api('/api/check', {source_url: $('url').value.trim()});
    let html = '<div style="margin-bottom:10px">' + (r.ok ? '<span class="ok">可用</span>' : '<span class="bad">不可用</span>') + '</div>';
    html += '<div class="kv">' + kv(r.rows && r.rows.length ? r.rows : [['错误', r.error || '—']]) + '</div>';
    show('链接检查', html);
  } catch (e) { show('检查失败', '<div class="bad">' + esc(e.message) + '</div>'); }
  finally { setBusy(false); }
};
$('apply').onclick = async () => {
  if (!PREVIEWED) return;
  if (!confirm('写入配置并' + ($('time').value !== (CURRENT && CURRENT.schedule.daily_time) ? '重装定时任务' : '更新图源') + '？')) return;
  setBusy(true);
  try {
    const r = await api('/api/apply', PREVIEWED);
    let html = '<div class="ok" style="margin-bottom:8px">已写入并校验通过</div><div class="kv">' + kv([
      ['备份', r.backup || '—'],
      ['定时任务', r.install ? (r.install.ok ? '已重装' : ('重装失败，配置已回滚' +
          (r.restore ? (r.restore.ok ? '，旧调度已恢复' : '，旧调度恢复也失败') : ''))) : '无需重装'],
    ]) + '</div>';
    if (r.install && r.install.output) html += '<pre style="margin-top:10px">' + esc(r.install.output) + '</pre>';
    show('应用结果', html);
    await loadStatus();
  } catch (e) { show('应用失败', '<div class="bad">' + esc(e.message) + '</div>'); }
  finally { setBusy(false); }
};
async function action(path, body, title){
  setBusy(true);
  try {
    const r = await api(path, body || {});
    show(title, '<div class="' + (r.ok ? 'ok' : 'bad') + '" style="margin-bottom:8px">' + esc(r.summary || (r.ok ? '完成' : '失败')) + '</div>' +
      (r.output ? '<pre>' + esc(r.output) + '</pre>' : ''));
    await loadStatus();
  } catch (e) { show(title + '失败', '<div class="bad">' + esc(e.message) + '</div>'); }
  finally { setBusy(false); }
}
$('run').onclick = () => action('/api/run', {force:false}, '运行结果');
$('update').onclick = () => { if (confirm('强制重新下载当前图源的图片？')) action('/api/run', {force:true}, '更新结果'); };
$('pause').onclick = () => action('/api/schedule', {action:'pause'}, '暂停');
$('resume').onclick = () => action('/api/schedule', {action:'resume'}, '恢复');
$('log').onclick = async () => {
  setBusy(true);
  try {
    const r = await api('/api/log?lines=200');
    $('logBody').textContent = r.text || '（日志为空）';
    $('logCard').classList.remove('hidden');
  } catch (e) { show('读取日志失败', '<div class="bad">' + esc(e.message) + '</div>'); }
  finally { setBusy(false); }
};
loadStatus();
</script></body></html>"""


def _run_cli(config_path, *args, timeout=300):
    """Drive the unchanged CLI in a child process and capture what it said."""
    command = [sys.executable, '-m', 'paperdrop', '--config', str(config_path), *args]
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


def status_payload(config_path):
    config, resolved = load_config(config_path)
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
        'schedule_state': _schedule_state(config),
        'state': _read_state(paths),
        'platform': platform,
        'log_file': str(paths.log_file),
    }


def classify_source(source_url, allow_http=False):
    """Fetch a candidate URL and say what it is. Values are plain text, never HTML."""
    scheme = source_url.split(':', 1)[0].lower()
    if scheme not in ('http', 'https'):
        return {'kind': None, 'rows': [], 'error': '必须是 http(s) 地址'}
    if scheme == 'http' and not allow_http:
        return {'kind': None, 'rows': [],
                'error': '明文 http 被配置拒绝（http.allow_http 为 false），请用 https'}
    deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
    try:
        opener = urllib.request.build_opener(_HttpOnlyRedirects)
        request = urllib.request.Request(source_url, headers={
            'User-Agent': 'PaperDrop/' + __version__, 'Accept': '*/*'})
        with opener.open(request, timeout=min(15, FETCH_DEADLINE_SECONDS)) as response:
            blocks, total = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError('source took longer than %ss' % FETCH_DEADLINE_SECONDS)
                block = response.read(256 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_FETCH:
                    raise ValueError('source is larger than %d bytes' % MAX_FETCH)
                blocks.append(block)
            payload = b''.join(blocks)
            content_type = response.headers.get('Content-Type', '')
            status = response.status
    except Exception as error:
        return {'kind': None, 'rows': [], 'url': source_url,
                'error': '%s: %s' % (type(error).__name__, error)}
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
    raw = json.loads(pathlib.Path(config_path).read_text(encoding='utf-8'))
    current_time = raw.get('schedule', {}).get('daily_time')
    current_source = raw.get('source', {})
    current_url = current_source.get('url')
    allow_http = bool(raw.get('http', {}).get('allow_http'))

    candidate = json.loads(json.dumps(raw))
    candidate.setdefault('schedule', {})['daily_time'] = daily_time
    candidate.setdefault('source', {})['url'] = source_url

    messages, ok = [], True
    if source_url != current_url:
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
        _validate(candidate)
    except Exception as error:
        ok = False
        messages.append({'level': 'error', 'text': str(error)})
    if str(source_url).lower().startswith('https://'):
        try:
            _reject_repository_page(source_url)
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
            'needs_install': daily_time != current_time,
            'candidate': candidate if ok else None}


def preview_changes(config_path, daily_time, source_url):
    prepared = _prepare(config_path, daily_time, source_url)
    prepared.pop('candidate', None)
    return prepared


def _atomic_write(path, text, mode=CONFIG_MODE):
    path = pathlib.Path(path)
    temporary = path.with_name(path.name + '.tmp-' + secrets.token_hex(6))
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
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


def apply_changes(config_path, daily_time, source_url):
    """Serialised: validate, back up, write atomically, re-read, reinstall, else restore."""
    config_path = pathlib.Path(config_path)
    # Classify before taking the write lock: a slow source must not block other writers.
    with _WRITE_LOCK:
        current = json.loads(config_path.read_text(encoding='utf-8'))
    verdict = None
    if source_url != current.get('source', {}).get('url'):
        verdict = classify_source(source_url, bool(current.get('http', {}).get('allow_http')))
    with _WRITE_LOCK:
        prepared = _prepare(config_path, daily_time, source_url, verdict=verdict)
        if not prepared['ok']:
            raise ValueError('; '.join(m['text'] for m in prepared['messages'] if m['level'] == 'error'))
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
                             "script-src 'unsafe-inline'; connect-src 'self'; "
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
                page = PAGE.replace('__PRESETS__', json.dumps(PRESETS, ensure_ascii=False)).encode()
                return self._send(200, page, 'text/html; charset=utf-8')
            if parsed.path == '/api/status':
                return self._send(200, status_payload(self.config_path))
            if parsed.path == '/api/log':
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
                                       str(body.get('source_url') or ''))
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


def serve(config_path=None, port=8765, open_browser=True):
    config_path = pathlib.Path(config_path).expanduser().resolve() if config_path else default_config_file()
    if not config_path.is_file():
        raise SystemExit('paperdrop: no config at %s (run "paperdrop install" first)' % config_path)
    load_config(config_path)  # fail fast on an invalid config
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
    print('open:   %s' % url)
    print('Ctrl-C to stop')
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
