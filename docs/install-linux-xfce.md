# Installing PaperDrop on Linux Mint (Xfce)

> **Verification status:** the shared pipeline has been exercised end-to-end on a
> development machine against real public sources. The Xfce-specific parts —
> backdrop property discovery, the background style, multiple monitors and
> workspaces, and the systemd user timer inside a graphical session — still need
> to be confirmed on a real Xfce desktop.

## Requirements

- Linux Mint with the **Xfce** desktop (other desktops are not supported yet).
- `xfconf-query`, which ships with Xfce. Check with:
  ```bash
  xfconf-query -c xfce4-desktop -l -v | head
  ```
- Python 3.9+ with `Pillow`. On Mint:
  ```bash
  sudo apt install python3-venv python3-pil
  ```
  (or use a venv and `pip install Pillow`).

## 1. Install the Python package

```bash
git clone https://github.com/PelikanM400/paperdrop.git
cd paperdrop
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/paperdrop --version
```

## 2. Understand the two Xfce specifics

**Property paths are discovered, not hardcoded.** Xfce stores the wallpaper in
`xfce4-desktop` properties named like
`/backdrop/screen0/monitor<name>/workspace<N>/last-image`. The monitor name and
the workspace index depend on your hardware and your workspace count, so
PaperDrop lists the channel at run time and sets every `/last-image` property it
finds. Inspect yours with:

```bash
xfconf-query -c xfce4-desktop -l -v | grep -E 'last-image|image-style'
```

**PaperDrop does not change your background style.** It only replaces the image
file, so the scaling mode you configured in *Desktop Settings → Background*
keeps applying. For "fill the screen, keep the aspect ratio, crop the excess",
choose the style labelled *Zoomed* once, and leave `apply.xfce_image_style` as
`null`.

If you want PaperDrop to manage the style as well, set `apply.xfce_image_style`
to the integer value of that style (the value next to `image-style` in the
`xfconf-query` output above).

## 3. Configure and run

```bash
mkdir -p ~/.config/paperdrop
.venv/bin/paperdrop --print-example-config > ~/.config/paperdrop/config.json
$EDITOR ~/.config/paperdrop/config.json      # set source.url
.venv/bin/paperdrop doctor
.venv/bin/paperdrop run
```

Run this **from your desktop session** (a terminal inside Xfce), not from an SSH
shell: `xfconf-query` needs `DISPLAY` and `DBUS_SESSION_BUS_ADDRESS` to reach
your session's `xfconfd`.

## 4. Install the schedule

```bash
.venv/bin/paperdrop install
```

This writes two files:

- `~/.config/systemd/user/paperdrop.service` — one-shot, runs `paperdrop run`
- `~/.config/systemd/user/paperdrop.timer` — `OnCalendar` from
  `schedule.daily_time`, with **`Persistent=true`**

`Persistent=true` is the catch-up: a run missed because the machine was off
fires once at the next boot or login.

```bash
systemctl --user status paperdrop.timer
systemctl --user list-timers | grep paperdrop
```

### If the timer fires but the wallpaper does not change

A user timer runs under `systemd --user`, which may not carry `DISPLAY`. Check
the journal:

```bash
journalctl --user -u paperdrop.service -n 50
```

If you see "No graphical session found", your desktop session is not exporting
the display to the user manager. Two workable answers:

1. Add a session autostart entry that runs `paperdrop run --check` once at login
   (the timer keeps doing the daily work when the session is up), or
2. run the sweep from cron with the environment captured explicitly:
   ```
   5 5 * * * DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus /path/to/.venv/bin/paperdrop run
   ```
   (cron does not read your shell environment, so the variables must be spelled
   out; verify the values with `echo $DISPLAY $DBUS_SESSION_BUS_ADDRESS` in your
   session before relying on them.)

## 5. Day-to-day

```bash
paperdrop status            # cache, last success, what the desktop shows
paperdrop update            # replace the wallpaper now
paperdrop pause             # systemctl --user disable --now paperdrop.timer
paperdrop resume
paperdrop apply-cached      # put the cached image back, no network
```

## 6. Uninstall

```bash
paperdrop uninstall           # disables the timer, deletes the unit files
paperdrop uninstall --purge   # also deletes the cached images
```

The wallpaper stays as it is; PaperDrop does not reset your desktop.

## What still needs real testing

- [ ] Does discovery find the right properties on your machine?
- [ ] Which `image-style` value gives "fill without stretching", on your desktop?
- [ ] Are **all** monitors updated (not just the primary one)?
- [ ] Are **all** workspaces updated (not just workspace 0)?
- [ ] Connect a new monitor: are properties created/subscribed correctly?
- [ ] Does the systemd user timer fire inside a graphical session?
- [ ] Does a run missed while the machine was off catch up at the next login?
- [ ] Does reconnecting an external display restore the wallpaper by itself
      (Xfce normally remembers per-monitor backdrops; confirm it does)?
- [ ] Do slide-show style background plugins conflict with PaperDrop?
