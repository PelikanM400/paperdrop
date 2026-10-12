# Mac 从 PaperDrop 0.1.6 升级到 0.1.8

适用于现有 pipx 安装。必须在 Mac 的桌面终端执行。**Python 包和 Mac 原生组件都要更新**；只升级 pipx 不够。不删除配置、缓存或状态。运行时间仍为 07:35。

> **为什么是 0.1.8**：0.1.7 里的 Swift 组件在 Apple Swift 6.4 上**编译不过**（`Darwin.flock(...)` 被解析成 `struct flock` 而不是 flock(2) 系统调用），所以 0.1.7 装了也没有原生组件，定时只会一直报失败。0.1.8 修好了这一处，Python 侧没有其他变化。
>
> 新版仍为预发布。离线测试通过不等于桌面已刷新；最后必须目视确认。

## 0. 先花 10 秒验证写法（可选但推荐）

```bash
mkdir -p "$HOME/paperdrop-probe" && cd "$HOME/paperdrop-probe" &&
cat > flock-probe.swift << 'EOF'
import Darwin
let fd = open("/tmp/paperdrop-flock-probe.lock", O_RDWR | O_CREAT, 0o600)
guard fd >= 0 else { fatalError("open failed") }
if flock(fd, LOCK_EX) != 0 { fatalError("flock LOCK_EX failed") }
_ = flock(fd, LOCK_UN)
close(fd)
print("PROBE OK: unqualified flock() compiles and runs on this toolchain")
EOF
swiftc -O -o flock-probe flock-probe.swift && ./flock-probe
```

应看到 `PROBE OK: ...`。如果是编译错误，**先停在这里**，把整段输出发给助手，不用继续下载。

## 1. 下载、核验并先编译（这一步不停止现有任务）

整个代码块一次粘贴：

```bash
mkdir -p "$HOME/paperdrop-upgrade-0.1.8" &&
cd "$HOME/paperdrop-upgrade-0.1.8" &&
curl -fL --retry 3 --connect-timeout 20 --max-time 180 -o paperdrop-0.1.8-py3-none-any.whl https://github.com/PelikanM400/paperdrop/releases/download/v0.1.8/paperdrop-0.1.8-py3-none-any.whl &&
curl -fL --retry 3 --connect-timeout 20 --max-time 180 -o paperdrop-0.1.8.tar.gz https://github.com/PelikanM400/paperdrop/releases/download/v0.1.8/paperdrop-0.1.8.tar.gz &&
curl -fL --retry 3 --connect-timeout 20 --max-time 180 -o SHA256SUMS https://github.com/PelikanM400/paperdrop/releases/download/v0.1.8/SHA256SUMS &&
shasum -a 256 -c SHA256SUMS &&
tar -xzf paperdrop-0.1.8.tar.gz &&
bash "$HOME/paperdrop-upgrade-0.1.8/paperdrop-0.1.8/macos/build.sh"
```

应该看到两个文件的 `OK`，以及最后的 `built: .../paperdrop-set`。如果下载、校验或 Swift 编译报错，**先停在这里，把输出发给助手，不继续第 2 步**。现有任务尚未暂停。

## 2. 暂停旧任务，升级 Python 与 Mac 组件

```bash
paperdrop pause &&
pipx install --force "$HOME/paperdrop-upgrade-0.1.8/paperdrop-0.1.8-py3-none-any.whl" &&
bash "$HOME/paperdrop-upgrade-0.1.8/paperdrop-0.1.8/macos/build.sh" --install &&
paperdrop --version
```

应该看到 `paperdrop 0.1.8`，以及 `installed: .../Library/Application Support/PaperDrop/paperdrop-set`。如果这一步失败，**不要继续装新定时，发出错输出**。任务此时可能暂停了。

## 3. 应用现有新图并恢复自动任务

```bash
paperdrop doctor
```

Mac helper 应能找到。若配置过 `apply.helper_path` 或自己在 PATH 放过另一份 helper，必须确认 doctor 选中的是刚更新的组件，不能配旧组件。

然后：

```bash
paperdrop apply-cached &&
paperdrop run --check &&
paperdrop install &&
launchctl kickstart gui/$(id -u)/dev.paperdrop.apply &&
sleep 5 &&
paperdrop status &&
launchctl list | grep paperdrop
```

第一条只使用已有图片，不重新下载。升级后 current/previous 会显示为 `image-<一串哈希>.jpg`，这是正常的新文件命名。不要手动删除或重命名这些文件。

## 4. 看实际桌面（不是只看 status）

- 今天应显示你刚才在“预览”看到的那张新图。
- 内置屏和外接屏分别检查；没有连接外接屏时先检查内置屏。
- 有多个桌面空间时切换检查。
- `dev.paperdrop.apply` 跑完后应是 `- 0`；watcher 应有 PID，未报错误退出。
- 把 `paperdrop status`、`launchctl list` 输出及“桌面是否真的换了”的目视结果发给助手。

首次视觉换图确认后，再测试拔插外接屏、休眠唤醒仍显示最新图。次日还须确认另一张图片能自动替换，不能以首装或状态路径成功代替连续日更新验收。

## 出错时

先不要删除缓存、改 DNS、反复 update 或重装旧版。把出错命令及完整输出发给助手。配置与源地址不需要更换。
