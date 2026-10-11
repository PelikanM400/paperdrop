"""Command line interface.

User-facing commands: run, update, status, install, uninstall, pause, resume,
doctor — plus apply-cached and watch, which the desktop schedule uses.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import __version__
from .config import ENV_CONFIG, ConfigError, example_config, load_config, validate
from .errors import BusyError, PaperDropError
from .journal import build_logger
from .lock import FileLock
from .paths import Paths
from .pipeline import apply_cached, run_once
from .platforms import detect_platform
from .state import State
from .cache import Cache

LOG = logging.getLogger('paperdrop')

EPILOG = ("PaperDrop ships no images and publishes nothing. Configure a public manifest "
          "or image URL; see the README for details.")


def build_parser():
    parser = argparse.ArgumentParser(prog='paperdrop', description='Daily wallpaper client '
                                     'for macOS and Linux (Xfce).', epilog=EPILOG)
    parser.add_argument('--version', action='version', version='paperdrop ' + __version__)
    parser.add_argument('--config', metavar='PATH',
                        help='config file (default: %s, or $%s)' %
                             ('~/.config/paperdrop/config.json', ENV_CONFIG))
    parser.add_argument('--verbose', action='store_true', help='more detail in the log')
    parser.add_argument('--json', action='store_true', help='machine-readable output')
    parser.add_argument('--print-example-config', action='store_true',
                        help='print a commented example config and exit')
    sub = parser.add_subparsers(dest='command')

    run = sub.add_parser('run', help="fetch and apply today's wallpaper")
    run.add_argument('--force', action='store_true', help='ignore the same-day check')
    run.add_argument('--check', action='store_true',
                     help='cheap run: repair the desktop state from the cache without downloading')

    update = sub.add_parser('update', help='replace the wallpaper now (same as "run --force")')
    update.set_defaults(force=True)

    sub.add_parser('status', help='show config, cache and desktop state')
    sub.add_parser('install', help='install the daily user-level schedule')
    uninstall = sub.add_parser('uninstall', help='remove the schedule (keeps the cache)')
    uninstall.add_argument('--purge', action='store_true', help='also delete the cached images')
    sub.add_parser('pause', help='stop the schedule without removing it')
    sub.add_parser('resume', help='restart a paused schedule')
    sub.add_parser('doctor', help='check this machine for the prerequisites')
    sub.add_parser('apply-cached', help='re-apply the cached image (no network)')
    sub.add_parser('watch', help='apply the cached image, then re-apply when screens change')
    # Accept --json/--verbose after the subcommand as well as before it.
    for child in sub.choices.values():
        child.add_argument('--json', action='store_true', default=argparse.SUPPRESS,
                           help=argparse.SUPPRESS)
        child.add_argument('--verbose', action='store_true', default=argparse.SUPPRESS,
                           help=argparse.SUPPRESS)
    return parser


def _load(args, allow_defaults=False) -> tuple:
    try:
        config, path = load_config(args.config)
        return config, path
    except ConfigError as error:
        if allow_defaults:
            fallback = validate(example_config())
            return fallback, str(error)
        raise


def _print(message):
    print(message)


def _json(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.print_example_config:
        print(json.dumps(example_config(), ensure_ascii=False, indent=2))
        return 0

    command = args.command or 'status'
    if args.command is None:
        parser.print_help()
        return 0

    build_logger(None, console=True, verbose=args.verbose)  # until we know the log path

    if command == 'doctor':
        return _doctor(args)

    try:
        config, config_path = _load(args)
    except ConfigError as error:
        print('paperdrop: %s' % error, file=sys.stderr)
        return 2

    paths = Paths.from_config(config, config_file=config_path)
    build_logger(paths.log_file, max_bytes=config['log']['max_bytes'], keep=config['log']['keep'],
                 verbose=args.verbose, console=args.verbose)
    LOG.debug('config loaded from %s', config_path)

    try:
        if command in ('run', 'update'):
            return _run(args, config, paths)
        if command == 'status':
            return _status(args, config, paths)
        if command == 'install':
            return _install(args, config, paths)
        if command == 'uninstall':
            return _uninstall(args, config, paths, purge=getattr(args, 'purge', False))
        if command in ('pause', 'resume'):
            return _toggle(args, config, paths, command)
        if command == 'apply-cached':
            return _apply_cached(args, config, paths)
        if command == 'watch':
            return _watch(args, config, paths)
    except PaperDropError as error:
        print('paperdrop: %s' % error, file=sys.stderr)
        return 1

    parser.print_help()
    return 0


def _platform(config):
    return detect_platform(config)


def _run(args, config, paths):
    force = bool(getattr(args, 'force', False))
    check_only = bool(getattr(args, 'check', False))
    lock = FileLock(paths.lock_file)
    try:
        lock.acquire()
    except BusyError as error:
        LOG.info('%s', error)
        if args.json:
            _json({'outcome': 'busy', 'detail': str(error)})
        else:
            _print('another PaperDrop run is in progress; nothing to do')
        return 0
    try:
        platform = _platform(config)
        result = run_once(config, paths, platform, force=force, check_only=check_only, logger=LOG)
    except PaperDropError as error:
        LOG.error('%s', error)
        if args.json:
            _json({'outcome': 'failed', 'detail': str(error)})
        else:
            _print('failed: %s' % error)
        return 1
    finally:
        lock.release()
    if args.json:
        _json(result.to_dict())
    else:
        _print('%s: %s' % (result.outcome, result.detail))
        for warning in result.warnings:
            _print('note: %s' % warning)
    return result.exit_code


def _status(args, config, paths):
    platform = _platform(config)
    state = State(paths.state_file).load()
    cache = Cache(paths.cache_dir)
    current, previous = cache.current(), cache.previous()
    payload = {
        'version': __version__,
        'config_file': str(paths.config_file),
        'source': {'mode': config['source']['mode'], 'url': config['source']['url']},
        'manifest_consistency': ('sha256 verified' if config['source']['mode'] == 'manifest'
                                 else 'NONE (image mode: weak guarantee)'),
        'cache_dir': str(paths.cache_dir),
        'current_image': str(current) if current else None,
        'previous_image': str(previous) if previous else None,
        'state_file': str(paths.state_file),
        'last_success': state.last_success,
        'last_attempt': state.last_attempt,
        'platform': platform.name,
        'desktop': platform.status_lines(),
        'scheduler': {'daily_time': config['schedule']['daily_time']},
    }
    if args.json:
        _json(payload)
        return 0
    _print('paperdrop %s' % __version__)
    _print('config:     %s' % paths.config_file)
    _print('source:     %s (%s)' % (config['source']['url'], config['source']['mode']))
    if config['source']['mode'] == 'image':
        _print('            WEAK GUARANTEE: image mode has no manifest consistency check')
    _print('cache dir:  %s' % paths.cache_dir)
    _print('current:    %s' % (current or 'none yet'))
    _print('previous:   %s' % (previous or 'none yet'))
    success = state.last_success
    if success:
        _print('last ok:    %s (%s)' % (success.get('finished_at'), success.get('local_date')))
        _print('            image %s' % success.get('image_sha256'))
        recorded_source = success.get('source_url')
        if recorded_source:
            note = ('' if recorded_source == config['source']['url'].strip()
                    else '   (the configured source has changed since then)')
            _print('            source %s%s' % (recorded_source, note))
    else:
        _print('last ok:    never')
    attempt = state.last_attempt
    if attempt and not attempt.get('ok'):
        _print('last try:   FAILED %s (%s)' % (attempt.get('reason'), attempt.get('finished_at')))
    elif attempt and attempt.get('outcome') == 'unchanged':
        _print('last check: %s (nothing to do: already up to date)'
               % attempt.get('finished_at'))
    for line in payload['desktop']:
        _print(line)
    return 0


def _install(args, config, paths):
    platform = _platform(config)
    created = platform.install(paths, config['schedule']['daily_time'])
    if args.json:
        _json({'installed': created, 'daily_time': config['schedule']['daily_time'],
               'platform': platform.name})
        return 0
    _print('installed the daily schedule (%s, %s):' % (config['schedule']['daily_time'],
                                                       platform.name))
    for path in created:
        _print('  %s' % path)
    _print('run "paperdrop status" to confirm, or "paperdrop run" for an immediate update.')
    return 0


def _uninstall(args, config, paths, purge=False):
    platform = _platform(config)
    removed = platform.uninstall(purge=purge)
    wiped = []
    if purge:
        wiped = Cache(paths.cache_dir).clear()
    if args.json:
        _json({'removed': removed, 'purged': wiped})
        return 0
    for path in removed:
        _print('removed %s' % path)
    if purge:
        _print('deleted %d cached file(s)' % len(wiped))
    else:
        _print('the cached wallpaper was kept (%s). Use "uninstall --purge" to delete it.' %
               paths.cache_dir)
    return 0


def _toggle(args, config, paths, command):
    platform = _platform(config)
    message = platform.pause() if command == 'pause' else platform.resume()
    if args.json:
        _json({'command': command, 'result': message})
        return 0
    _print(message)
    return 0


def _apply_cached(args, config, paths):
    platform = _platform(config)
    path = apply_cached(config, paths, platform, logger=LOG)
    if args.json:
        _json({'applied': str(path)})
        return 0
    _print('applied %s' % path)
    return 0


def _watch(args, config, paths):
    """Resident helper: apply the cache now, then re-apply on screen changes."""
    platform = _platform(config)
    if not platform.supports_screen_watch:
        print('paperdrop: this platform does not support screen watching yet.', file=sys.stderr)
        return 1
    path = apply_cached(config, paths, platform, logger=LOG)
    LOG.info('watching for screen configuration changes')
    return platform.watch(path)


def _doctor(args):
    config, config_error = _load(args, allow_defaults=True)
    paths = Paths.from_config(config, config_file=(args.config or paths_default()))
    rows = []
    rows.append(('configuration', config_error is None,
                 str(args.config) if config_error is None else config_error))
    rows.append(('python', sys.version_info >= (3, 9), sys.version.split()[0]))
    try:
        import PIL
        rows.append(('Pillow (image verification)', True, 'Pillow %s' % PIL.__version__))
    except ImportError:
        rows.append(('Pillow (image verification)', False,
                     'not installed: pip install Pillow (verification is required)'))
    try:
        platform = detect_platform(config)
        rows.append(('desktop platform', True, platform.name))
        rows.extend(platform.doctor())
    except PaperDropError as error:
        rows.append(('desktop platform', False, str(error)))
    try:
        Cache(paths.cache_dir).ensure()
        probe = paths.cache_dir / '.write-probe'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        rows.append(('cache directory writable', True, str(paths.cache_dir)))
    except OSError as error:
        rows.append(('cache directory writable', False, '%s (%s)' % (paths.cache_dir, error)))
    try:
        FileLock(paths.lock_file).acquire().release()
        rows.append(('state directory writable', True, str(paths.lock_file.parent)))
    except (OSError, PaperDropError) as error:
        rows.append(('state directory writable', False, str(error)))
    if args.json:
        _json([{'check': name, 'ok': ok, 'detail': detail} for name, ok, detail in rows])
    else:
        for name, ok, detail in rows:
            _print('%-32s %s  %s' % (name, 'ok  ' if ok else 'FAIL', detail))
        _print('\nPaperDrop never needs a token, and never clones or publishes a wallpaper '
               'repository.')
    return 0 if all(ok for _, ok, _ in rows) else 1


def paths_default():
    from .paths import default_config_file
    return default_config_file()
