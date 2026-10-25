# PaperDrop

**English** · [中文](#paperdrop纸落)

A small daily wallpaper client. Choose a public wallpaper source and a time;
PaperDrop downloads, verifies and applies the image for you. No account or API key is required.

## Install and configure

With Python and pipx available:

```bash
pipx install https://github.com/PelikanM400/paperdrop/releases/download/v0.3.0/paperdrop-0.3.0-py3-none-any.whl
pipx ensurepath
```

Open a new terminal, then run `paperdrop install`. The settings page opens in your
browser from a desktop terminal. Choose a source and time, check the settings,
then apply them to enable daily changes. Nothing is enabled before you confirm.
For prerequisites and setup details, see the [documentation](docs/README.md).

## Everyday commands

| Command | Purpose |
|---|---|
| `paperdrop update` | Fetch and apply the current wallpaper now. |
| `paperdrop upgrade` | Upgrade PaperDrop itself; open settings after a successful upgrade in an interactive desktop terminal. |
| `paperdrop status` | Show the current configuration and wallpaper status. |
| `paperdrop ui` | Open settings; on first use, configure the source and daily schedule. |

`upgrade` requires a standard pipx installation. Use `paperdrop upgrade --no-ui` to
skip the settings page. Closing the browser does not stop the local service;
press **Ctrl+C** in its terminal to exit. Settings listen on `127.0.0.1` only.

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

准备好 Python 和 pipx 后，运行：

```bash
pipx install https://github.com/PelikanM400/paperdrop/releases/download/v0.3.0/paperdrop-0.3.0-py3-none-any.whl
pipx ensurepath
```

打开一个新终端，然后运行 `paperdrop install`。在桌面终端运行后会自动打开设置页。
选择图源和时间，检查后点击应用，才会启用每日更换；确认前不自动启用。
环境准备和详细操作见[使用文档](docs/README.md)。

## 常用命令

| 命令 | 作用 |
|---|---|
| `paperdrop update` | 立即获取并更换壁纸。 |
| `paperdrop upgrade` | 升级纸落软件；在交互式桌面终端升级成功后自动打开设置页。 |
| `paperdrop status` | 查看当前配置和壁纸状态。 |
| `paperdrop ui` | 打开设置页；首次使用时设置图源和每日更换时间。 |

`upgrade` 适用于标准 pipx 安装；加 `--no-ui` 可跳过网页。关闭网页不会停止本地服务，在原终端按 **Ctrl+C** 退出。设置页仅监听本机 `127.0.0.1`。

## 项目说明

本项目由 AI agent 构建：Hermes 会话驱动 DeepSeek，OpenAI Codex/GPT 参与部分实现与排查；作者负责需求、验收和发布决定。发布经过独立 agent 复审和匿名安装包核验。

软件采用 [MIT 许可证](LICENSE)，随包思源黑体采用 SIL Open Font License。软件不附带壁纸，远程图片版权及使用许可归原权利人。
