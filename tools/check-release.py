#!/usr/bin/env python3
"""Check that the release source archive supports the documented Mac upgrade."""
import argparse
import pathlib
import tarfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=pathlib.Path)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    prefix = 'paperdrop-' + args.version + '/'
    required = (
        'macos/build.sh',
        'macos/Sources/paperdrop-set/main.swift',
        'docs/install-macos.md',
        'docs/upgrade-0.1.7-zh.md',
        'tests/support.py',
        'tests/fixtures/manifests/minimal.json',
        'tests/fixtures/manifests/not-json.txt',
        'src/paperdrop/cache.py',
        'src/paperdrop/pipeline.py',
        'src/paperdrop/platforms/macos.py',
        'pyproject.toml',
        'LICENSE',
    )
    with tarfile.open(args.archive) as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
    missing = [path for path in required if prefix + path not in names]
    if missing:
        parser.exit(1, 'Required source files missing: %s\n' % ', '.join(missing))
    print('Source archive acceptance: PASS (%d required files)' % len(required))


if __name__ == '__main__':
    main()
