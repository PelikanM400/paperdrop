# PaperDrop

**One image a day, from a source you choose, verified before it touches your desktop.**

PaperDrop is a small wallpaper client for **macOS** and **Linux (Xfce)**. You give
it a public wallpaper source URL; it fetches the latest image, verifies it,
caches it, and sets it as your desktop wallpaper on every screen.

It began as a personal tool for one NAS publishing a daily image, and is shared
in case that shape is useful to someone else.

---

## What it does — and what it does not

**Does:**

- Reads a public **manifest** (JSON) or a plain **image URL** that you configure.
- Verifies the download: HTTP success, declared size, **SHA-256**, full decode,
  pixel budget, and your minimum resolution.
- Caches the current and previous image, atomically. A failed download or a
  failed check **keeps your existing wallpaper**.
- Applies the image to every screen, scaled to fill without stretching and
  without spanning screens.
- Runs on your schedule, with a login catch-up and a same-day no-op path.
- Works with **no credentials at all**.

**Does not:**

- **Ship no images and host no gallery.** PaperDrop contains no artwork.
- **Never generates, uploads, publishes or hosts images.** It only reads from
  the source you configured, and it never writes to it.
- Never clones a wallpaper repository, and never synchronises a whole image
  library — just the one image you need.
- Never asks for a token, API key or account.

## Platform support and verification status

Honesty matters more than a longer feature list here.

| Platform | Status | Verified | Not yet verified |
|---|---|---|---|
| **macOS** | Implemented, **not yet verified on real hardware** | Nothing on a real Mac yet. The Swift helper and the LaunchAgents are written but have not been compiled or run. | dual-screen fill behaviour; **Spaces** (whether every desktop space updates); external-display reconnection; wake-from-sleep; LaunchAgent timing and login catch-up; Gatekeeper prompt for an unsigned binary |
| **Linux (Xfce)** | Implemented, **pending verification on a real Xfce desktop** | Core pipeline runs end-to-end against real public sources on the development machine (download, SHA-256 verification, decode, atomic cache, same-day no-op, failure handling). | real `xfconf-query` backdrop property discovery; the background-style value that means "fill without stretching"; multiple monitors and multiple workspaces; a newly attached monitor; the systemd user timer inside a graphical session |
| Both | The shared core: config, manifest parsing, transfer limits, verification, cache, state, lock, logging | Covered by the automated test suite (95 tests) — no network, no real desktop | — |

macOS Spaces is the one to be careful about: the AppKit call is **per screen**,
and the documentation does not promise anything about all Spaces. Treat it as
unverified until it has been tested on your machine.

## Quick start

### 1. Install

From the release page (a wheel and a source tarball are attached to each
release; no build tools needed for either):

```bash
pipx install https://github.com/PelikanM400/paperdrop/releases/download/v0.1.0/paperdrop-0.1.0-py3-none-any.whl
paperdrop --version
```

`pipx` keeps it in its own environment and puts `paperdrop` on your `PATH`. If
you would rather not use pipx, `pip install --user <the same wheel>` works too,
or install straight from the repository:

```bash
pipx install git+https://github.com/PelikanM400/paperdrop.git
```

From a checkout, if you want to run the tests or build the macOS helper:

```bash
git clone https://github.com/PelikanM400/paperdrop.git
cd paperdrop
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/paperdrop --version
```

`Pillow` is the only third-party dependency. It is required: a real decode is
what proves a truncated or fake file is not a wallpaper.

> **macOS users:** the release cannot include the AppKit helper — it must be
> compiled on your Mac, since Apple's SDK cannot be cross-compiled:
> `cd macos && ./build.sh --install`. See
> [docs/install-macos.md](docs/install-macos.md).

### 2. Write a config

```bash
mkdir -p ~/.config/paperdrop
paperdrop --print-example-config > ~/.config/paperdrop/config.json
$EDITOR ~/.config/paperdrop/config.json     # set source.url
```

### 3. Check it, then run it

```bash
paperdrop doctor            # is this machine ready?
paperdrop run               # fetch, verify, cache, apply
paperdrop status            # what is cached and what is on screen
```

### 4. Install the daily schedule

```bash
paperdrop install
```

- **macOS:** writes two LaunchAgents in `~/Library/LaunchAgents` — a daily run
  (plus login) and a resident watcher that re-applies the cached image when the
  display layout changes.
- **Linux/Xfce:** writes a systemd **user** service and timer with
  `Persistent=true`, so a run missed while the machine was off happens once at
  the next login.

## Configuring your wallpaper source

Two modes. Both are public-only and need no credentials.

### Manifest mode (recommended)

`source.url` points at a JSON manifest. Because the manifest carries a
`sha256`, PaperDrop can tell whether anything changed **without downloading the
image** — and it can prove that the bytes it cached are the ones the manifest
describes.

```json
{
  "source": {
    "mode": "manifest",
    "url": "https://example.com/wallpapers/desk.json"
  }
}
```

Prefer a URL that is pinned to an immutable revision (for example a commit
hash) so the manifest and the image can never disagree; see
[docs/manifest-spec.md](docs/manifest-spec.md).

### Image mode (weaker, opt-in)

`source.url` points straight at an always-updated image. This works, but it has
**no consistency guarantee**: nothing proves the bytes you received are the ones
the publisher intended, and an unchanged image may still have to be downloaded
to find out it is unchanged. PaperDrop refuses to run it until you acknowledge
that, and logs the warning on every run.

```json
{
  "source": {
    "mode": "image",
    "url": "https://example.com/wallpapers/desk.jpg",
    "image_mode_acknowledged": true
  }
}
```

### Not a valid source

A repository or directory page (`https://github.com/user/repo`, `.../tree/main`)
is not a wallpaper source — it is HTML. PaperDrop refuses it and tells you what
it expects instead. Point it at the manifest file or the image file itself.

### Checking a source before you commit to it

```bash
python3 tools/validate-manifest.py https://example.com/desk.json --check-image
```

This prints what the manifest declares and, with `--check-image`, downloads the
image to confirm the SHA-256 matches. It writes nothing to your cache.

## Commands

| Command | What it does |
|---|---|
| `paperdrop run` | Today's update. Does nothing if today already succeeded and the desktop still shows the cached image. |
| `paperdrop run --check` | Cheap pass: repair the desktop from the cache. Never downloads. |
| `paperdrop update` | Same as `run --force`: fetch and apply now, ignoring the same-day check. |
| `paperdrop status` | Config, cache, last success, what the desktop currently shows. |
| `paperdrop install` | Create the user-level schedule. |
| `paperdrop uninstall` | Remove the schedule. **Keeps** the cached wallpaper. |
| `paperdrop uninstall --purge` | Remove the schedule and delete the cached images. |
| `paperdrop pause` / `resume` | Stop or restart the schedule without removing it. |
| `paperdrop doctor` | Check Python, Pillow, the desktop, the helper and the writable paths. |
| `paperdrop apply-cached` | Re-apply the cached image, no network. |
| `paperdrop watch` | Apply the cache, then re-apply when the screen layout changes (macOS). |

Add `--json` for machine-readable output, anywhere on the command line.

## Where things live

| What | Path |
|---|---|
| Config | `~/.config/paperdrop/config.json` (`$PAPERDROP_CONFIG` overrides) |
| Current and previous image | `~/.cache/paperdrop/current.*`, `previous.*` |
| State | `~/.local/state/paperdrop/state.json` |
| Log (rotating, bounded) | `~/.local/state/paperdrop/paperdrop.log` |
| Lock | `~/.local/state/paperdrop/paperdrop.lock` |

Only two images are ever kept. Nothing is written outside your home directory.

## Privacy and security

- No account, no token, no API key, no telemetry.
- Only the source you configured is contacted. Followed redirects must stay on
  HTTPS, are limited in number, and never carry credentials.
- Downloads are size-capped; images are pixel-capped; the manifest is read with
  a small byte budget.
- Logs contain URLs and outcomes, never credentials (there are none to leak).

## Documentation

| Document | Contents |
|---|---|
| [docs/manifest-spec.md](docs/manifest-spec.md) | The manifest format, and the rules a client must follow |
| [docs/sources.md](docs/sources.md) | Choosing a source; manifest vs image mode |
| [docs/install-macos.md](docs/install-macos.md) | Building the helper, LaunchAgents, uninstall |
| [docs/install-linux-xfce.md](docs/install-linux-xfce.md) | Xfce specifics, the systemd user timer, uninstall |
| [docs/troubleshooting.md](docs/troubleshooting.md) | When the wallpaper does not change |

## Licence

MIT — see [LICENSE](LICENSE).

**The code licence does not cover remote wallpaper images.** PaperDrop ships no
images and hosts none. It downloads whatever source each user configures, and
the copyright and licence of those images stay with their owners. Choosing a
source you are allowed to use is the user's responsibility.

## Contributing

Issues and pull requests are welcome, especially reports from real hardware —
"this worked on a 2-monitor macOS setup" and "Spaces behaves like *this*" are
genuinely useful. The test suite runs offline:

```bash
python -m unittest discover -s tests -t tests
```

Please keep the project small and dependency-light: no GUI, no cloud service, no
gallery management, and no non-Xfce desktop environments (yet).
