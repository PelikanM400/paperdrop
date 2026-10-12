# Installing PaperDrop on macOS

> **Verification status:** the procedure below has been followed on a real Mac
> (MacBook Air M5, macOS 27.0.1): the helper compiles and runs, both screens are
> filled without stretching, every desktop space updates, an external display
> reconnected by hand comes back with the cached image, sleep/wake is fine, and
> the LaunchAgent runs and records each run. Still unverified: the calendar
> trigger firing at its time on its own, and the catch-up paths (see the
> checklist at the end).
>
> **Unreleased cache/watcher fix:** the previous real-Mac checks above do not
> verify this new implementation. Linux tests model pathname-cached pixels and
> check Swift source contracts; the updated helper still needs macOS compilation
> and visual wake/display verification.

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
paperdrop apply-cached   # after a first run; resolves the immutable current image
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
same source, PaperDrop reads its state and the current wallpaper — no network
request. If the desktop still matches it exits without re-applying; desktop drift
is repaired from the verified cache with bounded confirmation. Set
`catch_up_interval_minutes` to `0` if you
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

launchd never rotates the files it writes for you, so PaperDrop caps them itself:
each of the four files is trimmed back to about half of `log.max_bytes` (1 MB by
default) whenever it grows past that, keeping the newest lines and writing one
line to say what happened. Every run also checks this, so the total PaperDrop can
write stays at roughly 5 MB. In practice, with the hourly catch-up, expect about
a kilobyte a day.

Nothing sensitive is in them, and you can still clear them by hand whenever you
like:

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

## Upgrading from v0.1.6 fixed-path caches

This fix ships in **v0.1.7 (pre-release)**. Update Python and the native helper
together in the logged-in Mac session. For copy-and-paste release-asset steps,
see `upgrade-0.1.7-zh.md`; the source-checkout alternative is:

```bash
paperdrop pause                       # stops the old resident helper and schedule
cd /path/to/reviewed/paperdrop
pipx install --force .                # if the existing install uses pipx
bash macos/build.sh --install
paperdrop doctor                     # check the helper actually selected by the config/PATH
paperdrop run --check                 # offline; repairs a matching same-day legacy success
paperdrop apply-cached                # offline apply even if the saved success is older
paperdrop install                    # regenerate/reload agents with the upgraded Python install
paperdrop status --json
```

For a venv install use that environment's `pip install .` instead of pipx. If
`apply.helper_path` or a binary earlier on `PATH` overrides the installed helper,
replace that selected binary with the newly built one too. The old binary does
not understand `watch-cache`; merely installing Python is insufficient. Do not
purge the cache or delete `state.json` to work around this bug.

Migration hashes both legacy `current.*` / `previous.*` files, creates same-
filesystem hard links named `image-<sha256>.<extension>`, then atomically publishes
`images.json` containing both role filenames. The legacy names remain until the
index commits. A failed index write leaves both legacy files readable; after a
successful commit only the indexed images and index remain. Existing bytes are
not re-encoded. Role changes never rename an image file. Repeating identical
bytes reuses the same retained URL; publishing distinct bytes uses another URL.
Private staging can temporarily consume extra space. The next Python cache
operation prunes unindexed images left by an interrupted publication.

A same-day success with a legacy `cache_path` cannot prove visible pixels are
current. When the current bytes match its saved SHA-256, `run` and `run --check`
apply the new immutable URL without fetching, confirm the reported path and save
that new path only on success. Subsequent same-day checks remain no-ops and still
record `last check`. On rejection/failed confirmation the old current image file
remains retained; confirmed success is not falsely recorded. The atomic index's
`active` filename advances only after desktop confirmation, separate from a new
`current` candidate. If cleanup or rollback fails, the watcher still chooses the
confirmed active URL; Python restores that role before the next publication or
pruning. Missing/non-regular/symlink role targets fail closed without pruning
recoverable files. Failed cleanup can temporarily leave extra files until a
successful recovery. Ordinary same-day desktop drift also uses this offline
repair path. AppKit applies per
screen, so an error can still mean some screens changed — there is no atomic
multi-display visual rollback guarantee.

The resident helper receives the **cache directory** and re-reads the atomic
index at startup and every screen-change/wake callback. Python passes the exact
configured state-directory lock as `--lock-file`; each native event holds that
same flock only while resolving and applying, then releases it before returning
to the resident run loop. A callback waiting for an update resolves **after** the
update finishes, so it cannot restore a stale captured URL. Direct helper
`apply`/`current` calls do not acquire this lock again (Python already owns it).
The helper does not download. Legacy `watch <file>` also follows a migrated
index and now requires the explicit `--lock-file` argument.

### Required Mac acceptance for the v0.1.7 fix

- [ ] Recompile with the local Swift SDK; verify the selected helper recognizes
      `watch-cache` and the restarted LaunchAgent stays running.
- [ ] Upgrade with both legacy cached images present and a same-day recorded
      success. Confirm the SHA-256 of both migrated images is unchanged, the
      reported current URL is content-addressed, and **visually** confirm the
      new photo on both screens/all Spaces. Previewing the cache is not proof.
- [ ] With two different valid landscape images A and B in an isolated test
      config, apply A, leave the watcher resident, then update to B. Verify the
      URLs differ and A's retained bytes/name do not change. Reconnect the display
      and sleep/wake: both must keep B, never restore the startup image A.
- [ ] Repeat the same image: no new image data/URL and no needless same-day apply.
      After A→B→C only current and previous image data remain (plus the index).
- [ ] Simulate a rejected update in a test environment: preserve the old active
      file and no false `last ok`. Check for partial multi-screen changes.

Linux tests exercise a pathname-caching desktop model and actual Python cache/
state transitions. Adapter argv and Swift source contracts are checked, but
**none of those substitute for compilation, AppKit callbacks or visual proof**.

## 6. Uninstall

```bash
paperdrop uninstall           # removes both agents, keeps the cached wallpaper
paperdrop uninstall --purge   # also deletes the cached images
rm -rf "~/Library/Application Support/PaperDrop"   # optional: the helper binary
```

The wallpaper itself is left as it is.

## What still needs real testing

Confirmed on one machine (MacBook Air M5, macOS 27.0.1, built-in + 1920x1080
display):

- [x] Both screens are detected and filled with the same image without stretching.
- [x] The change applies to every desktop space, not just the current one.
- [x] Unplugging and reconnecting the external display restores the cached image.
- [x] Closing the lid and waking leaves the wallpaper intact.
- [x] The locally built helper ran without a Gatekeeper prompt (it was not
      quarantined, since it was compiled on the machine).
- [x] The LaunchAgent runs when it is loaded and when it is triggered by hand,
      exits 0, and records the run (`paperdrop status` shows a fresh
      `last check:`).

Still unknown — please report back on any of these:

- [ ] Does the daily LaunchAgent fire at `schedule.daily_time` on its own?
- [ ] Does the periodic catch-up fire after a wake from sleep?
- [ ] Does a missed run catch up at the next login?

### Test the schedule without waiting for it

Trigger the agent by hand and watch what it does:

```bash
launchctl kickstart -k gui/$(id -u)/dev.paperdrop.apply
sleep 5
paperdrop status                          # last check: should be seconds ago
launchctl list | grep paperdrop           # the second column must be 0
```

The second column of `launchctl list` is the last exit status. **`2` means the
scheduled command was rejected before it ran** — the classic cause is a
malformed argument list in the plist, which is why it is worth checking here
rather than assuming the schedule is fine. `tail -20
~/Library/Logs/dev.paperdrop.apply.err.log` shows the reason.
