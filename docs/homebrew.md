# Homebrew installation / Homebrew 安装

PaperDrop is available through the third-party `pelikanm400/tap` tap, not
Homebrew/core. This tap packages the project's pre-releases; installation and
command tests are distinct from real desktop/scheduler acceptance.

## First installation / 首次安装

1. Install Homebrew first if it is not already installed. On macOS the source
   build also needs Apple's Command Line Tools (`xcode-select --install`).
   Homebrew supplies its own Python and the Formula's declared image dependencies;
   no separate pipx or system-Python installation is needed. Linux desktop changes
   require a logged-in Xfce session and `xfconf-query` supplied by your OS.

   已有 Homebrew 即可。macOS 首次缺编译工具时运行 `xcode-select --install`，
   按系统窗口完成安装。Linux 仍需自己的 Xfce 桌面和 `xfconf-query`。

2. Install the package:

   ```bash
   brew install pelikanm400/tap/paperdrop
   paperdrop --version
   ```

   Expected: installation succeeds and the version is printed. On macOS the
   native `paperdrop-set` helper is compiled and installed by the Formula.
   This step does not create a configuration, start a UI, change wallpaper or
   install any launchd/systemd job. If compilation fails, stop and retain the full
   error; do not bypass it by reusing an unrelated old helper.

   应看到安装成功和版本号；此时不会创建配置、启动网页、换壁纸或启用定时。

3. In a terminal in your logged-in desktop session:

   ```bash
   paperdrop install
   ```

   Expected: local settings open in the browser. Choose the source and time,
   check, and apply to enable daily changes. Closing the browser alone does not
   stop settings; press Ctrl+C in the original terminal. SSH/CI/headless use does
   not automatically open settings. With an existing configuration, `install`
   installs/enables the schedule before opening settings.

   应看到本机设置页。先检查，再确认应用；只有确认后首次配置才会写入和启用定时。
   已有配置时 `install` 会安装并启用定时，不是单纯查看命令。

## Upgrading / 升级

```bash
brew update
brew upgrade pelikanm400/tap/paperdrop
paperdrop --version
```

Expected: the new version, or Homebrew reports that it is already current.
`paperdrop upgrade` in a Brew installation deliberately refuses self-update and
prints the Brew command instead. `paperdrop update` continues to mean wallpaper
update. Do not use pip/pipx to change files managed by Brew.

Brew upgrade does not change PaperDrop's source, time, configuration or paused
state, and does not automatically launch settings. Newly installed schedules use
Brew's stable `opt/paperdrop` path so cleaning an old Cellar version does not break
the next scheduled invocation. An already running watcher may still be the old
process until it is restarted (for example at next graphical login); package
upgrade alone is not a claim that a live watcher was restarted.

升级不改图源、时间、配置或暂停状态，也不自动打开设置页。不要为了升级额外执行
`paperdrop install`：该命令会启用定时。常驻 watcher 可能在下次图形登录时才换成新进程。

## Existing pipx users / 已用 pipx 的设备

Staying on pipx is supported. Do not install both copies and assume PATH chooses
the one you intended. Before migration, record `command -v paperdrop`,
`paperdrop --version` and `paperdrop status`, and back up the actual configuration
path shown by status. A custom `--config` path must remain explicit in all relevant
commands. Do not delete caches or configuration as part of migration.

After deliberately choosing migration, remove the old scheduler while the old
command still exists:

```bash
paperdrop uninstall
pipx uninstall paperdrop
```

Then follow first installation above. `paperdrop uninstall` removes PaperDrop's
scheduler entries but keeps config/cache (without `--purge`). `paperdrop install`
on the new installation enables scheduling, even if the old installation was
paused. To preserve a paused setup, use `paperdrop ui` for inspection rather than
running `install` until you explicitly want scheduling enabled. If a configuration
has a custom `apply.helper_path`, review it deliberately: that explicit setting
continues to override Brew's bundled helper.

迁移先记录配置与状态、备份，再卸旧调度和旧 pipx 包；不要同时保留两份同名命令。
原来暂停的设备不要执行 `install`，除非确实打算恢复定时。不要批量删除用户目录。

## Removing / 卸载

While PaperDrop is still installed, remove its user-level schedule first:

```bash
paperdrop uninstall
brew uninstall pelikanm400/tap/paperdrop
```

Expected: scheduler entries are removed, then the package is removed. Configuration
and cached images are retained. `brew uninstall` alone does not remove user-level
LaunchAgents/systemd entries. No tap install/uninstall hook silently edits HOME.
If you removed the package first and left a schedule, reinstall the package and run
`paperdrop uninstall` before removing it again.

先卸定时，再卸软件；配置和缓存保留。只运行 `brew uninstall` 不会替你删除用户调度。

## Acceptance / 验收

- `command -v paperdrop` points at the intended Brew installation, not an old pipx copy.
- `paperdrop --version` matches the Formula's version; the native helper exists on macOS.
- First setup remains explicit; opening settings alone does not enable scheduling.
- Software upgrades use Brew; configuration and paused state are preserved.
- Actual wallpaper changes, monitor reconnects, wake and scheduler execution must
  still be checked on the target desktop, not inferred from CI command tests.

Report failures with the exact command, full output, `brew config`,
`paperdrop --version` and (when configured) `paperdrop status`; do not include
credentials or private source URLs.
