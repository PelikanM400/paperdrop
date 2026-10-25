# PaperDrop

**English** · [中文](#paperdrop纸落)

A small daily wallpaper client. Choose a public wallpaper source and a time;
PaperDrop downloads, verifies and applies the image for you. No account or API key is required.

## Install and configure

With Homebrew available:

```bash
brew install pelikanm400/tap/paperdrop
```

Then run `paperdrop install` from a desktop terminal to open settings. Choose a
source and time, check the settings, then apply them to enable daily changes.
Nothing is enabled before you confirm. The macOS helper is built during Brew installation.
See the [Homebrew guide](docs/homebrew.md) for requirements, migration and removal;
[pipx and other setup methods](docs/README.md) remain available.

## Everyday commands

| Command | Purpose |
|---|---|
| `paperdrop update` | Fetch and apply the current wallpaper now. |
| `paperdrop upgrade` | Upgrade a standard pipx installation; Brew installations show the `brew upgrade` command instead. |
| `paperdrop status` | Show the current configuration and wallpaper status. |
| `paperdrop ui` | Open settings; on first use, configure the source and daily schedule. |

Brew users upgrade with `brew upgrade pelikanm400/tap/paperdrop`. With pipx,
`paperdrop upgrade --no-ui` skips settings. Closing the browser does not stop the
local settings service; press **Ctrl+C** in its terminal to exit. Settings listen
on `127.0.0.1` only.

## About

Built by AI agents through Hermes (DeepSeek, with OpenAI Codex/GPT participation);
the owner supplies requirements, accepts the result and decides releases.
Releases require independent agent review and anonymous artifact verification.

[MIT](LICENSE). The bundled Source Han Sans CN font uses the SIL Open Font License.
PaperDrop includes no wallpaper images; remote image rights remain with their owners.

---

# PaperDrop（纸落）

[English](#paperdrop) · **中文**

一个小型每日壁纸客户端。选择公开图源和更换时间，纸落会自动下载、校验并设置壁纸，不需要账号或 API 密钥。

## 安装与首次设置

已有 Homebrew 时运行：

```bash
brew install pelikanm400/tap/paperdrop
```

然后在桌面终端运行 `paperdrop install` 打开设置页。选择图源和时间，检查后点击应用，才会启用每日更换；确认前不自动启用。macOS 原生组件在 Brew 安装时编译，无需另行手动构建。
环境准备、旧安装迁移和卸载见 [Homebrew 指南](docs/homebrew.md)；[pipx 等安装方式](docs/README.md)继续保留。

## 常用命令

| 命令 | 作用 |
|---|---|
| `paperdrop update` | 立即获取并更换壁纸。 |
| `paperdrop upgrade` | 升级标准 pipx 安装；Brew 安装版会提示使用 `brew upgrade`。 |
| `paperdrop status` | 查看当前配置和壁纸状态。 |
| `paperdrop ui` | 打开设置页；首次使用时设置图源和每日更换时间。 |

Brew 安装版用 `brew upgrade pelikanm400/tap/paperdrop` 升级。pipx 版加 `--no-ui` 可跳过升级后的网页。关闭网页不会停止本地设置服务，在原终端按 **Ctrl+C** 退出。设置页仅监听本机 `127.0.0.1`。

## 项目说明

本项目由 AI agent 构建：Hermes 会话驱动 DeepSeek，OpenAI Codex/GPT 参与部分实现与排查；作者负责需求、验收和发布决定。发布经过独立 agent 复审和匿名安装包核验。

软件采用 [MIT 许可证](LICENSE)，随包思源黑体采用 SIL Open Font License。软件不附带壁纸，远程图片版权及使用许可归原权利人。
