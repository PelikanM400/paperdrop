# PaperDrop

**English** · [中文](#paperdrop-中文说明)

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
| **macOS** | Implemented; **partly verified on real hardware** | On one machine (MacBook Air M5, macOS 27.0.1, two displays): the Swift helper compiles and runs; both screens are detected and filled with the same image without stretching; **every desktop space updates**; an unplugged-and-reconnected external display comes back with the cached image; sleep and wake leave the wallpaper intact. | LaunchAgent timing, the login catch-up and the periodic catch-up (the schedule has not been exercised yet); the Gatekeeper prompt (the locally built helper was not quarantined on that machine) |
| **Linux (Xfce)** | Implemented, **pending verification on a real Xfce desktop** | Core pipeline runs end-to-end against real public sources on the development machine (download, SHA-256 verification, decode, atomic cache, same-day no-op, failure handling). | real `xfconf-query` backdrop property discovery; the background-style value that means "fill without stretching"; multiple monitors and multiple workspaces; a newly attached monitor; the systemd user timer inside a graphical session |
| Both | The shared core: config, manifest parsing, transfer limits, verification, cache, state, lock, logging. Plus the platform artifacts we cannot run here: launchd plists, systemd units, Xfce property handling | Covered by the automated test suite (120 tests) — no network, no real desktop | — |

macOS Spaces is the one to be careful about: the AppKit call is **per screen**,
and Apple's documentation promises nothing about all Spaces. One real machine
reported that every space does update — useful, but one data point, not a
guarantee. Verify it on your own machine.

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
> `cd macos && bash build.sh --install`. See
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

**Neither scheduler wakes a sleeping machine.** On macOS a calendar trigger does
not fire while the Mac is asleep, so the daily agent also carries a periodic
catch-up (`schedule.catch_up_interval_minutes`, 60 by default) that picks the run
up soon after the machine wakes. Those extra runs are nearly free: when today
already succeeded, PaperDrop only checks the desktop state — no download, no
re-apply. Set the interval to `0` to turn them off. On Linux, `Persistent=true`
is what handles a run missed while the machine was off.

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

Changing the source later takes effect on the next `paperdrop run`: the
same-day shortcut is keyed on the source URL, so a new source is never masked
by "already up to date today". `paperdrop status` prints the source that the
last success came from.

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

---

# PaperDrop 中文说明

[English](#paperdrop) · **中文**

**每天一张图，来源由你指定；先校验，再动你的桌面。**

PaperDrop 是一个小型的**每日壁纸客户端**，支持 **macOS** 与 **Linux（Xfce）**。你给它一个公开的壁纸源地址，它每天取回最新图片，校验后缓存，并把它设为你所有显示器的桌面壁纸。

它最初只是一个人自用的小工具（服务端每天发布一张图），整理后开源，或许这种形态对别人也有用。

---

## 它做什么，以及它不做什么

**它做：**

- 读取你配置的公开**清单（JSON）**，或一个普通的**图片直链**。
- 校验下载内容：HTTP 成功、声明的大小、**SHA-256**、**完整解码**、像素上限，以及你设置的最低分辨率。
- 以原子方式缓存"当前 + 上一张"两张图。下载失败或校验失败时，**保留你原有的壁纸**。
- 把图片应用到**每一块显示器**：等比填充、不拉伸、不跨屏。
- 按你设定的时间运行，支持登录后补检查，以及"当天已成功就跳过"的轻量路径。
- **完全不需要任何凭据。**

**它不做：**

- **不附带任何图片，不提供图库。** 仓库里没有任何美术资源。
- **不生成、不上传、不发布、不托管任何图片。** 它只读取你配置的源，且从不向源写入。
- 不克隆壁纸仓库，也不同步整个图库——只要你需要的那一张。
- 从不索取令牌、API 密钥或账号。

## 平台支持与验证状态

这里如实说明比把功能表写长更重要。

| 平台 | 状态 | 已验证 | 尚未验证 |
|---|---|---|---|
| **macOS** | 已实现；**已在真实设备上部分验证** | 在一台机器上（MacBook Air M5、macOS 27.0.1、两块屏）：Swift 小工具编译并运行成功；两块屏都被正确识别，同一张图各自铺满且未拉伸；**每个桌面空间都会更新**；外接显示器拔掉再插回后仍是缓存图片；合盖休眠再唤醒后壁纸完好。 | LaunchAgent 到点触发、登录补检查与周期补检查（定时尚未实测）；未签名二进制的 Gatekeeper 提示（该机器上本地编译的工具未被隔离） |
| **Linux（Xfce）** | 已实现，**待在真实 Xfce 桌面上验证** | 共享流水线已在开发机上对接**真实公开源**跑通（下载、SHA-256 校验、解码、原子缓存、当天跳过、失败处理） | 真实 `xfconf-query` 背景属性发现；"等比填充不拉伸"对应的样式取值；多显示器与多工作区；新接入的显示器；systemd user timer 在图形会话中的运行 |
| 两者共有 | 共享核心：配置、清单解析、传输限制、校验、缓存、状态、锁、日志；以及本机无法运行的平台产物（launchd plist、systemd 单元、Xfce 属性处理） | 自动化测试覆盖（120 项，不联网、不依赖真实桌面） | — |

需要特别留意的是 macOS 的 **Spaces**：AppKit 的接口是**按屏幕**设置的，苹果官方文档并未承诺"所有桌面空间都会更新"。
有一台真实机器反馈"每个空间都会更新"——这是有用的数据点，但不是普遍保证。请在你自己的机器上实测。

## 快速开始

### 1. 安装

从 Release 页面安装（每个版本都附带 wheel 安装包与源码包，都不需要编译工具）：

```bash
pipx install https://github.com/PelikanM400/paperdrop/releases/download/v0.1.0/paperdrop-0.1.0-py3-none-any.whl
paperdrop --version
```

`pipx` 会把它装在独立环境里，并把 `paperdrop` 命令放进 `PATH`。不想用 pipx 也可以 `pip install --user <同一个 wheel>`，或者直接从仓库安装：

```bash
pipx install git+https://github.com/PelikanM400/paperdrop.git
```

如果你想跑测试或自己编译 macOS 小工具，则从源码检出：

```bash
git clone https://github.com/PelikanM400/paperdrop.git
cd paperdrop
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/paperdrop --version
```

唯一的第三方依赖是 `Pillow`。它是**必需**的：只有真正解码一遍，才能确认截断的文件或伪装的 HTML 不是一张壁纸。

> **macOS 用户请注意：** Release 里**不可能**包含 AppKit 小工具——苹果的 SDK 无法交叉编译，必须在你的 Mac 上编译一次：
> `cd macos && bash build.sh --install`。详见 [docs/install-macos.md](docs/install-macos.md)。

### 2. 写配置

```bash
mkdir -p ~/.config/paperdrop
paperdrop --print-example-config > ~/.config/paperdrop/config.json
$EDITOR ~/.config/paperdrop/config.json     # 设置 source.url
```

### 3. 检查环境，然后运行

```bash
paperdrop doctor            # 这台机器准备好了吗？
paperdrop run               # 获取 → 校验 → 缓存 → 设为壁纸
paperdrop status            # 缓存里有什么、桌面上现在是什么
```

### 4. 安装每日定时

```bash
paperdrop install
```

- **macOS：** 在 `~/Library/LaunchAgents` 写入两个 LaunchAgent——一个每日运行（并且登录时也跑一次），一个常驻进程在显示器布局变化时重新应用缓存图片。
- **Linux/Xfce：** 写入 systemd **用户级** service 与 timer，使用 `Persistent=true`，因此关机期间错过的运行会在下次登录时补上一次。

**两边的调度都不会唤醒睡眠中的电脑。** macOS 的定时触发器在睡眠期间**不会触发**，
所以每日任务还带一个周期性补检查（`schedule.catch_up_interval_minutes`，默认 60 分钟），
醒来自动补上。这些补检查几乎零成本：当天已成功时只检查桌面状态，不下载、不重设。
设为 `0` 可关闭。Linux 侧靠 `Persistent=true` 处理"关机期间错过"的情况。

## 配置你的壁纸源

两种模式，都只针对公开源，都不需要凭据。

### 清单模式（推荐）

`source.url` 指向一个 JSON 清单。由于清单里带 `sha256`，PaperDrop 可以**在不下载图片的情况下**判断内容是否变化；也能证明它缓存下来的字节正是清单所描述的那一张。

```json
{
  "source": {
    "mode": "manifest",
    "url": "https://example.com/wallpapers/desk.json"
  }
}
```

建议使用指向**不可变版本**的地址（例如带提交哈希的固定地址），这样清单与图片永远不会互相错位。详见 [docs/manifest-spec.md](docs/manifest-spec.md)。

以后换源会在下一次 `paperdrop run` 时自动生效："当天已成功"的判断与源地址绑定，
换了源不会被"今天已经更新过"掩盖。`paperdrop status` 会打印上一次成功所用的源地址。

### 图片模式（较弱，需显式确认）

`source.url` 直接指向一张持续更新的图片。这能工作，但**没有任何一致性保障**：无法证明你收到的字节就是发布方想要的那一张；而且即使图片没变，也往往得先下载一遍才能知道。PaperDrop 在你明确确认之前拒绝运行这种模式，并且**每次运行都会记录一条弱保障告警**。

```json
{
  "source": {
    "mode": "image",
    "url": "https://example.com/wallpapers/desk.jpg",
    "image_mode_acknowledged": true
  }
}
```

### 不能当作源的东西

仓库首页或目录页（`https://github.com/user/repo`、`.../tree/main`）**不是**壁纸源——那是 HTML。PaperDrop 会拒绝它，并告诉你应该填什么：清单文件地址，或图片文件本身的地址。

### 配置前先自检一个源

```bash
python3 tools/validate-manifest.py https://example.com/desk.json --check-image
```

它会打印清单声明的各项内容；加上 `--check-image` 还会下载图片来核对 SHA-256。**不会**写入你的缓存。

## 命令

| 命令 | 作用 |
|---|---|
| `paperdrop run` | 执行今天的更新。若今天已成功且桌面仍显示缓存图片，则什么都不做。 |
| `paperdrop run --check` | 轻量检查：只用缓存修复桌面状态，**从不下载**。 |
| `paperdrop update` | 等同于 `run --force`：立刻获取并应用，忽略"当天已成功"判断。 |
| `paperdrop status` | 显示配置、缓存、上次成功、桌面当前状态。 |
| `paperdrop install` | 创建用户级定时。 |
| `paperdrop uninstall` | 移除定时，**保留**缓存壁纸。 |
| `paperdrop uninstall --purge` | 移除定时并删除缓存图片。 |
| `paperdrop pause` / `resume` | 暂停／恢复定时，不删除配置。 |
| `paperdrop doctor` | 检查 Python、Pillow、桌面环境、小工具、可写路径。 |
| `paperdrop apply-cached` | 重新应用缓存图片，不联网。 |
| `paperdrop watch` | 先应用缓存，之后在屏幕布局变化时重新应用（macOS）。 |

`--json` 可加在命令行任意位置，输出机器可读格式。

## 文件位置

| 内容 | 路径 |
|---|---|
| 配置 | `~/.config/paperdrop/config.json`（可用 `$PAPERDROP_CONFIG` 覆盖） |
| 当前与上一张图片 | `~/.cache/paperdrop/current.*`、`previous.*` |
| 状态 | `~/.local/state/paperdrop/state.json` |
| 日志（滚动、有上限） | `~/.local/state/paperdrop/paperdrop.log` |
| 锁文件 | `~/.local/state/paperdrop/paperdrop.lock` |

**最多只保留两张图片**，且不会写到你主目录之外的任何位置。

## 隐私与安全

- 没有账号、没有令牌、没有 API 密钥、没有遥测。
- 只访问你配置的那个源。跟随跳转时仅允许 HTTPS、限制跳转次数，且**绝不携带凭据**。
- 下载有体积上限；图片有像素上限；清单读取也有小的字节预算。
- 日志只包含地址与结果，不含任何凭据（本来就没有凭据可泄漏）。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/manifest-spec.md](docs/manifest-spec.md) | 清单格式，以及客户端必须遵守的规则 |
| [docs/sources.md](docs/sources.md) | 如何挑选源；清单模式与图片模式的区别 |
| [docs/install-macos.md](docs/install-macos.md) | 编译小工具、LaunchAgent、卸载 |
| [docs/install-linux-xfce.md](docs/install-linux-xfce.md) | Xfce 相关细节、systemd user timer、卸载 |
| [docs/troubleshooting.md](docs/troubleshooting.md) | 壁纸没有变化时怎么办 |

> 上述文档目前只有英文版本。

## 许可证

MIT，见 [LICENSE](LICENSE)。

**代码许可证不覆盖远程壁纸图片。** PaperDrop 不附带图片、不托管图片；它下载的是每个使用者自己配置的源，那些图片的版权与许可仍属于其权利人。**选用你有权使用的源，是使用者自己的责任。**

## 参与贡献

欢迎提 Issue 和 Pull Request，**尤其是来自真实设备的反馈**——"双屏 macOS 上跑通了""Spaces 的实际表现是这样"这类信息非常有价值。测试套件完全离线运行：

```bash
python -m unittest discover -s tests -t tests
```

请让这个项目保持小而轻：不加图形界面、不接云服务、不做图库管理，也暂时不扩展到 Xfce 之外的桌面环境。
