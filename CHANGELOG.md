# Changelog

All notable changes to PaperDrop are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.9] — 2026-10-12

Still a pre-release: Python regressions are exercised off-device; the Swift helper
requires local compilation and real-Mac visual acceptance.

### Fixed
- `paperdrop doctor` reported `configuration FAIL <path>` for a perfectly good
  config file and exited 1, because it read the second element of `_load()` —
  the *path* on success — as if it were an error object. Diagnostics now load
  through `_load_or_defaults()`, which returns `(config, config_file, error)` and
  says explicitly whether the example config was substituted. Anyone following the
  upgrade guide with `paperdrop doctor && …` was stopped at the first step.

## [0.1.8] — 2026-10-12

Still a pre-release: Python regressions are exercised off-device; the Swift helper
requires local compilation and real-Mac visual acceptance.

### Fixed
- The Mac helper did not compile at all with swiftc 6.4 (Apple Swift 6.4).
  `Darwin.flock(fd, LOCK_EX)` resolved to `struct flock` rather than the flock(2)
  syscall, so the build stopped with "argument passed to call that takes no
  arguments" and produced no helper; v0.1.7 could only report `last try: FAILED`
  once its schedule was reinstalled. The two calls are now unqualified, which is
  what makes the compiler select the function, and a source-contract test keeps
  the qualified form from returning. Compilation remains unverified off-device.

## [0.1.7] — 2026-10-12

Still a pre-release: Python regressions are exercised off-device; the updated
Swift helper requires local compilation and real-Mac visual acceptance.

### Fixed
- macOS could keep showing old pixels after `current.jpg` was replaced, even
  while AppKit reported that same pathname as current. Verified images now use
  immutable `image-<sha256>.<extension>` URLs with an atomic current/previous
  index; changing roles never renames image files. Only two images are retained
  after publication/recovery (private staging can temporarily use extra space).
- Existing v0.1.6 current/previous images migrate without downloading or changing
  their bytes. A same-day success recorded against a legacy fixed path forces
  one offline repair, including `run --check`, rather than repeating false success.
- The resident macOS helper reads the latest current index on every wake/display
  event, instead of capturing its startup image. Display notifications use the
  application notification center; wake notifications use the workspace center.
- Rejected/unconfirmed updates keep the old current image URL retained, including
  repeated failures and failed cleanup/rollback writes. The index separately
  retains the confirmed active URL; candidates cannot advance it until desktop
  confirmation. Cache IO failures record a failed attempt without hiding the
  original error behind a second cleanup failure.
- Cache role targets must exist as regular non-symlink files before any pruning;
  invalid targets fail closed without removing recoverable images.
- A legacy fixed-name entry (`current.*` / `previous.*`) that is a symlink or a
  directory instead of a regular file is now a hard error: nothing is migrated
  and nothing is pruned around it, so the image such an entry points at can no
  longer be deleted by the migration.
- A failure while writing the failure record no longer replaces the real error:
  the write is retried once carrying the original reason, which is kept in both
  the reported detail and the persisted `last_attempt`.
- Native resolve/apply callbacks take the exact configured updater flock for
  each event, not for the resident lifetime; waiting callbacks resolve afresh.
- Ordinary same-day desktop drift is repaired offline with bounded confirmation,
  without resolving or downloading the source.
- The source release includes the Swift helper, build script, upgrade guide and
  test fixtures. Release-archive acceptance checks these files before publishing.

### Upgrade / verification
- Rebuild and install the Swift helper and restart the resident watcher along
  with upgrading Python; the previous helper lacks the new `watch-cache` command.
- Path-caching desktop model, migration, pruning, failure and adapter tests run
  offline. Swift source assertions are **not** compilation, AppKit execution or
  visual proof. This change still needs macOS compilation and real-device checks.

## [0.1.6] — 2026-10-11

**Critical: the daily schedule never ran, on either platform.**

### Fixed
- The generated scheduler entry passed `--config` **after** the verb
  (`paperdrop run --config X`), but it is a global option, so argparse rejected
  it: the job exited 2 on every fire on both macOS (LaunchAgent) and Linux
  (systemd unit). It also failed before writing anything to PaperDrop's own log
  or state, so the schedule looked like it was simply doing nothing.
  The option now precedes the verb in both the plist and the unit file.

### Added
- The test suite now **executes** the generated LaunchAgent `ProgramArguments` as
  a subprocess, so argument order and parseability are covered rather than mere
  presence; the systemd `ExecStart=` order is asserted too.

## [0.1.5] — 2026-10-11

### Added
- **A hard ceiling on everything PaperDrop can write to disk**: about 5 MB in
  total by default. A bug that logs in a loop can no longer fill a disk.
  `log.max_bytes` is now 1 MB (was 256 KB) and `log.keep` stays 2, so the
  rotating log tops out at 3 MB. On macOS the four files launchd writes for the
  scheduled runs are capped at 1 MB each — launchd never rotates them, so
  PaperDrop trims the oldest half off whenever one goes over, at every entry
  point, including the resident watcher.

### Fixed
- The "no supported desktop" message named a hardcoded `v0.1.0` no matter what
  was running; it now reports the running version.
- Rebuilding the logger no longer leaves the previous log file open.

### Fixed
- The "no supported desktop" error message named a stale version (`v0.1.0`) from
  a hardcoded string. It now reports the version that is actually running, and a
  test pins it there.

## [0.1.4] — 2026-10-11

### Added
- **Every run now leaves a trace**, so "is my schedule firing?" has an answer.
  A run that finds today's image already in place used to touch nothing on disk,
  which made the question impossible to answer without reading a log file.
  `paperdrop status` prints `last check:` — the most recent run that had nothing
  to do. `last ok` keeps its stricter meaning: the last time the wallpaper
  actually changed.

## [0.1.3] — 2026-10-11

### Changed
- Default `schedule.daily_time` is now `07:35` (was `05:05`) — the author's own
  preference for both platforms. Anyone who wants a different time sets it in
  their config; a config that omits `schedule` now picks this up.
- README and the macOS install guide record the first real-hardware results:
  dual-screen fill, every desktop space updating, external-display reconnection,
  sleep/wake, and no Gatekeeper prompt for the locally built helper were all
  confirmed on one machine (MacBook Air M5, macOS 27.0.1). The scheduled run
  itself is still unverified, and that stays marked as such.

## [0.1.2] — 2026-10-11

Found by running PaperDrop on a real Mac for the first time. Still a
pre-release: most device behaviour remains unverified.

### Fixed
- **A successful apply was reported as a failure** when the desktop took a
  moment to report the new wallpaper. macOS does this: it accepts the change and
  keeps answering with the old file for a moment. The confirmation now re-reads
  the desktop state `apply.confirm_attempts` times, `apply.confirm_delay_seconds`
  apart (5 x 0.8 s by default) before giving up.
- Changing `source.url` is no longer masked by the same-day shortcut; it takes
  effect on the next `paperdrop run`.
- `paperdrop run --check` really never downloads now, as documented.
- A missing config file prints the two commands that create one, instead of
  pointing at a file that does not exist yet.
- `macos/build.sh` is executable in git, and the docs use
  `bash build.sh --install`, which survives a source tarball losing the bit.
- Xfce adapter: the `xfconf-query` and `systemctl` paths are injectable rather
  than looked up on every call, so a lookup failure can no longer mask a session
  problem.

### Added
- **macOS sleep handling.** launchd does not fire a calendar trigger while the
  Mac is asleep, so the daily agent also carries a periodic catch-up
  (`schedule.catch_up_interval_minutes`, 60 minutes by default; `0` disables it).
  Those runs are nearly free — with today already done there is no download and
  no re-apply. `paperdrop status` now reports the schedule.
- Off-device tests for the platform artifacts: the LaunchAgent plists (daily
  trigger, login, catch-up interval, Aqua session), the systemd unit and timer
  text, Xfce property discovery across monitors and workspaces, style
  preservation, and the enable/disable flows — all through fake command runners.
  The suite is now 120 tests.
- `paperdrop status` prints the source that the last success came from, and
  flags it when it no longer matches the configured source.
- Troubleshooting entries for `pipx: command not found` after `pipx ensurepath`,
  for macOS systems with only the Command Line Tools Python 3.9 (supported), and
  for the desktop-state confirmation above.

### Changed
- When the configured source changes but resolves to the same image (the same
  wallpaper published to two hosts), the run says so instead of a bare
  "already up to date".
- README is bilingual: English first, Chinese below.

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
