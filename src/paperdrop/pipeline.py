"""The daily run: resolve, verify, cache, apply — and stay cheap when nothing changed.

Ordering rules (deliberate):

1. A run that already succeeded today only re-checks the desktop state; it does
   not download and does not force the wallpaper to be re-applied.
2. When the source gives a SHA-256 and the cached image already matches it, the
   image is not downloaded again.
3. Bytes are verified *before* they are published into the cache.
4. Any failure keeps the previous wallpaper.
"""
from __future__ import annotations

import datetime
import logging
import pathlib
import time
from dataclasses import dataclass, field
from typing import Optional

from .cache import Cache
from .errors import PaperDropError
from .fetch import Fetcher
from .sources import Target, resolve_source
from .state import State, now_iso
from .util import sha256_hex
from .verify import check_bytes, check_image, extension_for

LOGGER_NAME = 'paperdrop'


@dataclass
class RunResult:
    outcome: str                      # unchanged | updated | reapplied | failed
    detail: str = ''
    identity: str = ''
    cache_path: str = ''
    downloaded: bool = False
    applied: bool = False
    wrote_remote: bool = False        # always False: PaperDrop never publishes
    checks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return self.outcome != 'failed'

    @property
    def exit_code(self):
        return 0 if self.ok else 1

    def to_dict(self):
        return {
            'outcome': self.outcome,
            'detail': self.detail,
            'identity': self.identity,
            'cache_path': self.cache_path,
            'downloaded': self.downloaded,
            'applied': self.applied,
            'checks': list(self.checks),
            'warnings': list(self.warnings),
        }


def _display_matches(platform, expected_path):
    try:
        applied = platform.current()
    except PaperDropError as error:
        return False, 'could not read the desktop state: %s' % error
    if not applied:
        return False, 'the desktop did not report a wallpaper'
    if str(applied) != str(expected_path):
        return False, 'the desktop shows %s' % applied
    return True, 'the desktop shows the cached image'


def _confirm_display(platform, expected_path, attempts, delay, sleep):
    """Wait a little for the desktop to report the new image.

    Not every desktop answers immediately: macOS accepts the change at once but
    can keep returning the previous file for a moment, which used to turn a
    successful apply into a reported failure.
    """
    attempts = max(1, int(attempts))
    detail = 'the desktop did not report a wallpaper'
    for attempt in range(attempts):
        matched, detail = _display_matches(platform, expected_path)
        if matched:
            return True, detail
        if attempt + 1 < attempts:
            sleep(delay)
    return False, detail


def _cached_sha(path):
    try:
        return sha256_hex(path.read_bytes())
    except OSError:
        return None


def _perform_run(config, paths, platform, fetcher=None, now=None, force=False,
                 check_only=False, logger=None, sleep=None):
    """The run itself. Never raises for expected failures; returns a RunResult."""
    logger = logger or logging.getLogger(LOGGER_NAME)
    sleep = sleep or time.sleep
    attempts = config['apply']['confirm_attempts']
    delay = config['apply']['confirm_delay_seconds']
    started = now or datetime.datetime.now().astimezone()
    today = started.date().isoformat()
    cache = Cache(paths.cache_dir)
    state = State(paths.state_file).load()
    fetcher = fetcher or Fetcher.from_config(config)
    success = state.last_success
    source_url = config['source']['url'].strip()

    # --- 1. nothing to do when today already succeeded FROM THE SAME SOURCE ----
    # The shortcut is keyed on the source too: otherwise pointing PaperDrop at a
    # different source would be silently masked by "already up to date today".
    same_source = bool(success) and success.get('source_url') == source_url
    source_changed = bool(success) and not same_source
    if success and not same_source and success.get('local_date') == today:
        logger.info('the configured source changed since the last success; '
                    'the same-day shortcut does not apply')
    if not force and same_source and success.get('local_date') == today:
        cached = pathlib.Path(success.get('cache_path', ''))
        cached_ok = cached.is_file() and _cached_sha(cached) == success.get('image_sha256')
        if cached_ok:
            matches, detail = _display_matches(platform, cached)
            if matches:
                return RunResult('unchanged', 'already up to date for %s (%s)' % (today, detail),
                                 identity=success.get('identity', ''), cache_path=str(cached),
                                 checks=['desktop_state'])
            if check_only:
                # A cheap repair: put the cached image back. No download, no re-selection.
                logger.info('desktop state needs repair: %s', detail)
                platform.apply(cached)
                matches, detail = _confirm_display(platform, cached, attempts, delay, sleep)
                if matches:
                    state.record_success(**dict(success, finished_at=now_iso(),
                                                local_date=today, checks=['reapplied']))
                    return RunResult('reapplied', 're-applied the cached image',
                                     identity=success.get('identity', ''), cache_path=str(cached),
                                     applied=True, checks=['desktop_state', 'reapplied'])
                state.record_failure('the desktop state could not be confirmed', today=today)
                return RunResult('failed', 'the cached image was applied but the desktop state '
                                           'could not be confirmed', checks=['desktop_state'])

    # --- 1b. --check never downloads: it either repaired above or reports -------
    if check_only:
        cached = cache.current()
        detail = ('check only: the cached image does not match the configured source'
                  if cached is not None else 'check only: there is no cached image yet')
        logger.info('%s; run "paperdrop run" for a full update', detail)
        return RunResult('unchanged', detail, cache_path=str(cached) if cached else '',
                         checks=['check_only'])

    # --- 2. resolve the source ------------------------------------------------
    try:
        target = resolve_source(config, fetcher)
    except PaperDropError as error:
        state.record_failure(str(error), today=today)
        return RunResult('failed', str(error))

    for warning in target.warnings:
        if target.mode == 'image' or 'photo_id' not in warning:
            logger.warning('%s', warning)
        else:
            logger.info('%s', warning)
    logger.info('source resolved: %s (%s)', target.describe(), target.mode)

    cached = cache.current()
    cached_sha = _cached_sha(cached) if cached is not None else None
    already_have_it = bool(target.sha256 and cached_sha and cached_sha == target.sha256)

    # --- 3. download only when needed ----------------------------------------
    downloaded = False
    try:
        if already_have_it and not force:
            image_path, image_sha = cached, cached_sha
            logger.info('cached image already matches the source sha256; not downloading')
        else:
            data, image_sha, image_path, downloaded = _fetch_and_publish(
                config, target, cache, fetcher, state, force, logger)
    except PaperDropError as error:
        state.record_failure(str(error), today=today, identity=target.identity)
        return RunResult('failed', str(error), identity=target.identity,
                         cache_path=str(cached) if cached else '')

    # --- 4. apply and confirm -------------------------------------------------
    try:
        platform.apply(image_path)
    except PaperDropError as error:
        state.record_failure(str(error), today=today, identity=target.identity)
        return RunResult('failed', str(error), identity=target.identity,
                         cache_path=str(image_path), downloaded=downloaded)
    matches, detail = _confirm_display(platform, image_path, attempts, delay, sleep)
    if not matches:
        reason = ('the image was cached but the desktop state could not be confirmed (%s)'
                  % detail)
        state.record_failure(reason, today=today, identity=target.identity)
        return RunResult('failed', reason,
                         identity=target.identity, cache_path=str(image_path),
                         downloaded=downloaded, applied=False)
    recorded = state.record_success(
        identity=target.identity, image_sha256=image_sha, cache_path=str(image_path),
        source_url=target.source_url, mode=target.mode, local_date=today,
        published_at=target.published_at, source_date=target.date,
        etag=getattr(fetcher, 'last_etag', None), last_modified=getattr(fetcher, 'last_last_modified', None),
        checks=['desktop_state'])
    outcome = 'updated' if downloaded else 'unchanged'
    if downloaded:
        detail = 'downloaded and applied a new image'
    elif source_changed:
        # Common when the same image is published to two hosts: nothing to fetch,
        # but say so instead of a bare "unchanged".
        detail = ('the configured source changed, and it describes the same image, so the '
                  'cached copy was kept and re-applied')
    else:
        detail = 'the cached image was already current and the desktop state was confirmed'
    return RunResult(outcome, detail, identity=target.identity, cache_path=str(image_path),
                     downloaded=downloaded, applied=True, checks=['desktop_state'],
                     warnings=list(target.warnings))


def _fetch_and_publish(config, target, cache, fetcher, state, force, logger):
    """Download, verify, then publish into the cache. Returns (data, sha, path, downloaded)."""
    cached = cache.current()
    conditional = None
    # A conditional request is about HTTP caching, not about the same-day check, so it
    # still applies when the user forces a refresh.
    if target.mode == 'image':
        previous = state.last_success or {}
        if previous.get('source_url') == target.url and (previous.get('etag') or previous.get('last_modified')):
            conditional = (previous.get('etag'), previous.get('last_modified'))
    fetcher.last_etag = None
    fetcher.last_last_modified = None
    if conditional:
        logger.info('asking the source whether the image changed (conditional request)')
        response = fetcher.get_conditional(target.url, etag=conditional[0],
                                           last_modified=conditional[1])
        if response.not_modified and cached is not None:
            logger.info('source reported 304 Not Modified; keeping the cached image')
            return cached.read_bytes(), _cached_sha(cached), cached, False
    else:
        response = fetcher.get(target.url)
    fetcher.last_etag = response.etag
    fetcher.last_last_modified = response.last_modified
    digest = check_bytes(response.data, expected_sha256=target.sha256,
                         expected_size=target.size_bytes)
    width, height, image_format = check_image(
        response.data, expected_width=target.width, expected_height=target.height,
        min_width=config['source']['min_width'], min_height=config['source']['min_height'],
        max_pixels=config['verify']['max_pixels'])
    logger.info('verified image: %dx%d %s sha256=%s', width, height, image_format, digest[:12])
    extension = extension_for(image_format, url=target.url, content_type=response.content_type)
    path = cache.publish(response.data, extension)
    return response.data, digest, path, True


def apply_cached(config, paths, platform, logger=None):
    """Apply the cached image without any network access (used by the screen watcher)."""
    logger = logger or logging.getLogger(LOGGER_NAME)
    cache = Cache(paths.cache_dir)
    cached = cache.current()
    if cached is None:
        raise PaperDropError('No cached image yet; run "paperdrop run" first.')
    platform.apply(cached)
    logger.info('applied the cached image %s', cached)
    return cached


def run_once(config, paths, platform, fetcher=None, now=None, force=False,
             check_only=False, logger=None, sleep=None):
    """Perform one run, recording even the runs that had nothing to do.

    Wrapping it here (rather than at each early return inside) keeps one rule:
    every run leaves a trace, so "did the schedule fire?" is answerable without
    reading a log file.
    """
    result = _perform_run(config, paths, platform, fetcher=fetcher, now=now, force=force,
                          check_only=check_only, logger=logger, sleep=sleep)
    if result.outcome == 'unchanged':
        State(paths.state_file).load().record_noop('unchanged', result.detail,
                                                   identity=result.identity,
                                                   cache_path=result.cache_path)
    return result
