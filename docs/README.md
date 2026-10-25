# PaperDrop usage / 使用文档

The README contains only the installation entry point and everyday commands.
Use these guides for prerequisites, native components, scheduling and troubleshooting.

README 只保留安装入口和四个常用命令；环境准备、原生组件、定时与故障处理放在这里。

- [macOS setup and native helper](install-macos.md)
- [Linux Xfce setup](install-linux-xfce.md)
- [Wallpaper sources](sources.md)
- [JSON manifest format](manifest-spec.md)
- [Troubleshooting](troubleshooting.md)
- [Changelog](../CHANGELOG.md)
- [Release notes and known limitations](https://github.com/PelikanM400/paperdrop/releases)

## First-time configuration

After installing the package, open a new terminal (run `pipx ensurepath` first if
`paperdrop` is not found) and run `paperdrop install`. `paperdrop ui` also opens
local settings even when no configuration exists. Opening or checking settings does
not enable a schedule. Apply a checked configuration to save it and install the
daily schedule.
The settings process stays in its terminal until Ctrl+C; closing the browser alone
does not exit it. The website is local-only, protected by a random per-run URL token.

For an existing configuration, interactive `paperdrop install` installs the schedule
and then opens settings. On first use without a config, it opens configuration first.
`paperdrop install --no-ui` retains the direct, noninteractive scheduler operation;
it requires an existing valid configuration. JSON output, SSH, CI and noninteractive
terminals do not automatically open or run settings.

首次使用时运行 `paperdrop ui`，即可在网页里配置；打开、检查不会启用定时，确认应用后才保存并安装。
`paperdrop install --no-ui` 是已有配置的无网页调度安装入口，供脚本使用。

## Software upgrades versus wallpaper updates

- `paperdrop update`: force a wallpaper fetch/apply, not a software upgrade.
- `paperdrop upgrade`: check official GitHub releases, verify the selected release
  assets, then upgrade a standard pipx installation. Published pre-releases are included
  while PaperDrop remains on its pre-release channel. No downgrade is performed.
- `paperdrop upgrade --no-ui`: the same upgrade without starting settings.

An automatic settings page is launched only after a version change succeeds, and only
from an interactive desktop terminal (not SSH, CI, JSON mode or a headless session).
The new interpreter starts the new UI; existing source, time and paused state are preserved.
Unsupported install layouts are refused with a manual upgrade message rather than using
system pip or sudo. Upgrading from versions that do not yet include `upgrade` still requires
one manual pipx installation of the new wheel first.

## Additional commands

The command-line help lists all options. Advanced commands remain available:
`run`, `run --check`, `doctor`, `install --no-ui`, `uninstall`, `pause`, `resume`,
`apply-cached`, and `watch`. `uninstall` keeps cached wallpaper unless explicitly
called with `--purge`.

## Scope and release validation

Platform-specific prerequisites and limitations belong to the setup guides and release
notes, not to a broad cross-platform guarantee in the README. Native helper compilation,
OS desktop behaviour and OS scheduling need target-device evidence. Independent review
and anonymous download/install verification do not replace that evidence.

### Development page override

`PAPERDROP_UI_PAGE=/path/page.html` loads a local HTML override on each refresh.
Missing, unreadable, non-UTF-8 or oversized files fall back to the packaged page.
The override retains local request guards. Bundled pages/fonts are served locally;
remote wallpaper licensing is separate from the code and bundled font licences.
