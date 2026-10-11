# Changelog

All notable changes to PaperDrop are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- A missing config file now prints the two commands that create one (`mkdir -p`
  plus `--print-example-config`), instead of pointing at a file that does not
  exist yet. (On main, after v0.1.1.)

### Added
- When the configured source changes but the new source describes the same
  image (for example the same wallpaper published to two hosts), the run says so
  instead of a bare "already up to date". (Not in v0.1.1; it landed after that
  tag, so it will ship in the next release.)

## [0.1.1] — 2026-10-11

Two behaviour fixes found while setting PaperDrop up on a real Mac, plus the
documentation that came out of it. Still a pre-release: device behaviour on
real hardware is not verified yet.

### Fixed
- Changing `source.url` is no longer masked by the same-day shortcut. The
  "already succeeded today" check is now keyed on the source as well, so
  pointing PaperDrop at a different source takes effect on the next `run`
  instead of silently reporting "already up to date".
- `paperdrop run --check` now really never downloads, matching what the README
  has always claimed. Previously a missing or changed cached image made it fall
  through to a full update; it now reports that a full run is needed.
- `macos/build.sh` is recorded as executable in git, and the documentation uses
  `bash build.sh --install`, which works even when a source tarball loses the
  executable bit.

### Added
- `paperdrop status` prints the source recorded with the last success, and says
  so when it no longer matches the configured one.
- Troubleshooting entries for `pipx: command not found` right after
  `pipx ensurepath`, and for macOS systems that only have the Command Line
  Tools Python 3.9 (which is supported; pip selects the last compatible
  Pillow, 11.3.x).

### Changed
- README is now bilingual: English first, with a Chinese translation below.
  (Documentation only; no code or asset change, so v0.1.0 was not re-released.)

## [0.1.0] — 2026-10-11

First release. Scope deliberately small: one image a day, two platforms.

Published as a **pre-release**: the shared core is finished and exercised, but
the platform-specific behaviour on real hardware has not been verified yet.

### Added
- Manifest mode: read a JSON manifest that carries `image_url` plus `sha256`,
  verify the download against it, and skip the download entirely when the
  cached image already matches.
- Image mode: accept a plain always-updated image URL. Explicit opt-in, logged
  as a weak guarantee on every run, because there is no manifest to prove that
  the bytes match what the publisher intended.
- Refusal to treat a repository or directory page as a wallpaper source.
- Verification before publication: declared size, SHA-256, full decode, pixel
  budget, and the configured minimum resolution.
- Cache holding the current and the previous image, published atomically; a
  failed download or check never replaces the working wallpaper.
- Same-day idempotence: once today's run succeeded, a repeated run only checks
  the desktop state and repairs it from the cache without touching the network.
- Multi-screen support: one image on every screen, each scaled to fill without
  stretching and without spanning screens.
- `paperdrop run | update | status | install | uninstall | pause | resume |
  doctor | apply-cached | watch`.
- macOS adapter: a small AppKit helper plus user-level LaunchAgents (daily run,
  login catch-up, resident screen-change watcher).
- Xfce adapter: `xfconf-query` against dynamically discovered backdrop
  properties, with a systemd `--user` timer using `Persistent=true` for missed
  runs.
- Single-instance lock, bounded retries with backoff, rotating log.

### Notes
- No token, no credential, no repository cloning, no gallery synchronisation.
  The client only reads the public source the user configured.
- macOS: dual-screen fill, Spaces behaviour, external-display reconnection and
  wake-from-sleep have **not** been verified on real hardware yet. See the
  verification table in the README.
- Linux: verified against a real Xfce desktop only after the acceptance
  checklist in `docs/` has been run; the property paths are discovered, not
  hardcoded.
- Unknown platform behaviour is documented as unverified rather than assumed.
