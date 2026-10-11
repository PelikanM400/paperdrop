# Changelog

All notable changes to PaperDrop are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use
[Semantic Versioning](https://semver.org/).

## [0.1.0] — unreleased

First release. Scope deliberately small: one image a day, two platforms.

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
