# Installing PaperDrop on macOS

> **Verification status:** nothing in this document has been verified on a real
> Mac yet. The helper has not been compiled, and dual-screen behaviour, Spaces,
> external-display reconnection and wake-from-sleep are all unverified. Treat
> this as the intended procedure, not as a tested one.

## Requirements

- macOS with the Swift toolchain and the macOS SDK. The Command Line Tools are
  enough:
  ```bash
  xcode-select --install
  swiftc --version
  ```
- Python 3.9 or newer with `Pillow` (macOS ships Python 3 with the Command Line
  Tools).
- A graphical session. PaperDrop sets the wallpaper through AppKit, which needs
  your logged-in desktop session — a plain SSH shell is not enough.

## 1. Install the Python package

From a checkout:

```bash
git clone https://github.com/PelikanM400/paperdrop.git
cd paperdrop
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/paperdrop --version
```

Note the interpreter path (`.venv/bin/python`); the installer records it in the
LaunchAgent so the scheduled run uses the same environment.

## 2. Build the AppKit helper

The helper is a single Swift file that does the two things only AppKit can do:
apply one image per screen, and re-apply it when the display configuration
changes.

```bash
cd macos
bash build.sh --install     # builds, then copies to ~/Library/Application Support/PaperDrop/
```

Use the `bash build.sh` form: it works whether or not the file kept its
executable bit (`./build.sh` is equivalent when the bit is set, which it is in
a normal clone).

`paperdrop` finds it there automatically; alternatively put it on `PATH` or set
`apply.helper_path` in the config.

Quick manual check (safe — it only touches your wallpaper):

```bash
./paperdrop-set screens
./paperdrop-set apply "$HOME/.cache/paperdrop/current.jpg" --fill   # after a first run
./paperdrop-set current
```

`--fill` scales proportionally and clips, so the image fills the screen without
being stretched. `--fit` keeps the whole image visible and may leave bars.

## 3. Configure and run

```bash
mkdir -p ~/.config/paperdrop
.venv/bin/paperdrop --print-example-config > ~/.config/paperdrop/config.json
$EDITOR ~/.config/paperdrop/config.json      # set source.url
.venv/bin/paperdrop doctor
.venv/bin/paperdrop run
```

## 4. Install the schedule

```bash
.venv/bin/paperdrop install
```

Two LaunchAgents are written to `~/Library/LaunchAgents`:

| Label | Purpose |
|---|---|
| `dev.paperdrop.apply` | Runs at login (`RunAtLoad`), daily at `schedule.daily_time`, and periodically as a catch-up. |
| `dev.paperdrop.watch` | Stays resident and re-applies the cached image when the display layout changes or the machine wakes (`KeepAlive`). |

### Sleep, wake and missed runs

launchd does **not** fire a calendar trigger while the Mac is asleep, and it does
not wake the machine to run the job. So the daily agent carries a second trigger,
`StartInterval`, set from `schedule.catch_up_interval_minutes` (60 minutes by
default). After the Mac wakes, the daily run happens within that interval.

Those extra runs are cheap on purpose: when today has already succeeded from the
same source, PaperDrop reads its state and the current wallpaper and exits — no
network request and no re-applying. Set `catch_up_interval_minutes` to `0` if you
would rather have only the exact daily time plus the login run.

Two consequences worth knowing:

- If the Mac is asleep at `schedule.daily_time` and you never log in, the image
  updates when the machine next wakes and the interval elapses.
- The resident watcher (`dev.paperdrop.watch`) re-applies the **cached** image on
  wake; it does not fetch a new one. That is deliberate: waking should not
  trigger downloads.

Both are limited to the `Aqua` session type, which is what makes AppKit work.
Logs go to `~/Library/Logs/dev.paperdrop.apply.{out,err}.log`, and PaperDrop's
own rotating log is at `~/.local/state/paperdrop/paperdrop.log`.

PaperDrop's own log rotates and is size-capped. The two launchd logs are **not**
rotated by PaperDrop: launchd appends one short line per run, so with the hourly
catch-up enabled expect roughly a kilobyte a day. They contain nothing sensitive
and are safe to delete or truncate at any time:

```bash
: > ~/Library/Logs/dev.paperdrop.apply.out.log
```

Change the time by editing `schedule.daily_time` and running `paperdrop install`
again, then:

```bash
launchctl list | grep paperdrop
```

## 5. Day-to-day

```bash
paperdrop status            # cache, last success, what the screens show
paperdrop update            # replace the wallpaper now
paperdrop pause             # stop the schedule (unloads both agents)
paperdrop resume
paperdrop apply-cached      # put the cached image back, no network
```

## 6. Uninstall

```bash
paperdrop uninstall           # removes both agents, keeps the cached wallpaper
paperdrop uninstall --purge   # also deletes the cached images
rm -rf "~/Library/Application Support/PaperDrop"   # optional: the helper binary
```

The wallpaper itself is left as it is.

## What still needs real testing

Please report back on any of these; each is currently unknown:

- [ ] Does the image fill **both** screens correctly (internal + external)?
- [ ] **Spaces**: does the change apply to every desktop space, or only some?
- [ ] Unplug and reconnect the external display — is the cached image re-applied?
- [ ] Close the lid and wake — is the wallpaper still correct, and does the
      watcher survive?
- [ ] Does the daily LaunchAgent fire at `schedule.daily_time`?
- [ ] Does a missed run catch up at the next login?
- [ ] Does an unsigned binary trigger a Gatekeeper prompt, and what does the user
      see?
