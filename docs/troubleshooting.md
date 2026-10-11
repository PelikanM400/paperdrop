# Troubleshooting

Start with:

```bash
paperdrop doctor
paperdrop status
tail -n 50 ~/.local/state/paperdrop/paperdrop.log
```

`doctor` tells you whether the machine is ready; `status` tells you what is
cached, what succeeded last, and what the desktop currently reports.

## The wallpaper did not change

1. **Is today's run recorded?** `paperdrop status` shows the last success and its
   date. PaperDrop deliberately does nothing when today already succeeded *and*
   the desktop still shows the cached image.
   Note that this shortcut is keyed on the source URL: if you changed
   `source.url`, the next `paperdrop run` updates from the new source instead of
   reporting "already up to date". `paperdrop status` shows the source the last
   success came from, and flags it when it differs from the configured one.
2. **Did the source actually change?** Compare the cached image with the source:
   ```bash
   python3 tools/validate-manifest.py <your manifest URL> --check-image
   ```
   If the hash matches what you already have, there is nothing new to apply.
3. **Force it once:**
   ```bash
   paperdrop update
   ```
4. **Check which file the desktop thinks it has.** `paperdrop status` prints it.
   If the desktop shows a different path, the apply step is not reaching the
   desktop — see the platform sections below.

## "the desktop state could not be confirmed"

The image was cached and the apply command was accepted, but reading back the
current wallpaper did not return the cached file. The previous wallpaper is left
alone and the run exits non-zero.

- **Linux/Xfce:** usually means PaperDrop is not running inside your graphical
  session. `echo $DISPLAY` and `echo $DBUS_SESSION_BUS_ADDRESS` should both be
  set. Run it from your desktop session, or use the installed systemd user timer.
- **macOS:** the helper could not read the wallpaper back, which usually means it
  is not running in your Aqua session. LaunchAgents installed by
  `paperdrop install` run there; a plain SSH session does not.

## Linux: "No Xfce backdrop properties were found"

PaperDrop discovers the backdrop properties instead of hardcoding them, because
they embed the monitor name and the workspace index. If none are found:

```bash
xfconf-query -c xfce4-desktop -l -v | grep backdrop
```

- If that prints nothing, `xfdesktop` is probably not running in this session, or
  you are not in a graphical session.
- If it prints properties, please open an issue with the output: the discovery
  pattern may need to cover your layout.

## Linux: the wallpaper changes on one workspace only

Each workspace has its own backdrop property. PaperDrop sets every discovered
`/backdrop/.../last-image` property, so all workspaces should follow. If one does
not, check whether it appeared after the last run:

```bash
paperdrop update && xfconf-query -c xfce4-desktop -l -v | grep last-image
```

## Linux: the image is stretched, or has bars

PaperDrop intentionally **does not** change your background *style*: it replaces
the image file and leaves the scaling mode you configured. To get "fill the
screen, keep the aspect ratio, crop the excess", set that style once in
*Desktop Settings → Background → Style* (in Xfce the fitting choice is usually
labelled *Zoomed*).

If you would rather have PaperDrop set the style too, set
`apply.xfce_image_style` to the integer style value. Find it with:

```bash
xfconf-query -c xfce4-desktop -l -v | grep image-style
```

## macOS: "the macOS helper paperdrop-set was not found"

Build it on the Mac (it cannot be cross-compiled):

```bash
cd macos && bash build.sh --install
```

This needs the Command Line Tools (`xcode-select --install`). Afterwards
`paperdrop doctor` should report the helper path.

## macOS: "pipx: command not found" right after installing pipx

Expected, and nothing is broken. `pipx ensurepath` edits your shell
configuration, but the terminal window you are sitting in was started before
that edit, so it does not know the new path yet.

Two ways out — either is fine:

```bash
# 1. run pipx by its full path for this one command (no new window needed)
~/Library/Python/3.9/bin/pipx install <the wheel URL>

# 2. or open a brand-new terminal window, then "pipx ..." works normally
```

After a `pipx install`, the `paperdrop` command lands in `~/.local/bin`, which
is normally already on `PATH`; if it is not found either, use
`~/.local/bin/paperdrop` or open a new terminal window.

## macOS: only Python 3.9 is available (the Command Line Tools build)

That is enough — no need to install another Python. PaperDrop supports Python
3.9+, and on 3.9 pip automatically selects the last compatible Pillow (11.3.x)
instead of the newest one. This combination is tested: the full suite and a
real end-to-end run pass on 3.9.

## macOS: nothing happens at the scheduled time

- Check that the agent is installed: `paperdrop status` lists the LaunchAgents.
- `launchctl list | grep paperdrop` shows whether they are loaded.
- The daily agent also runs at login (`RunAtLoad`), which is the catch-up path.
- A LaunchAgent only runs inside your Aqua session; it does not run while you are
  logged out.

## macOS: behaviour across Spaces

The AppKit call PaperDrop uses sets the wallpaper **per screen**. Whether every
desktop space follows is not promised by the API and is currently unverified in
this project. If you test it, a report would be very welcome.

## "Refusing to fetch a http URL"

Plain HTTP is off by default. If your source really is HTTP-only, set
`"http": {"allow_http": true}` — but prefer HTTPS: the image is the thing you are
going to look at all day, and plain HTTP can be tampered with in transit.

## "That looks like a repository or directory page"

You pointed `source.url` at something like `https://github.com/user/repo`. Point
it at the manifest file or the image file itself.

## "Manifest declares schema_version N ... Upgrade PaperDrop"

Your source uses a newer manifest format than this client understands. Upgrade
PaperDrop rather than working around it: silently misreading an unknown format is
worse than stopping.

## "Image is WxH, below the configured minimum"

Your `source.min_width` / `source.min_height` are higher than the image. Either
lower them, or pick a source that publishes larger images — a small image
stretched over a 4K screen looks bad, which is what the check exists to prevent.

## Downloads fail only sometimes

PaperDrop retries transient failures (timeouts, `429`, `5xx`) with backoff, and
does not retry permanent ones (`404`, hash mismatch). A failing run keeps the
previous wallpaper and exits non-zero, and the scheduler reports the failure.
Increase `http.retries` or `http.timeout_seconds` for a slow host.

## Uninstalling

```bash
paperdrop uninstall            # removes the schedule, keeps the cached wallpaper
paperdrop uninstall --purge    # also deletes the cached images
```

Your wallpaper stays as it is either way — uninstalling does not reset the
desktop to some default.
