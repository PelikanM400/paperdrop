# 升级到 0.1.12（macOS · 中文）

**这次是纯 Python 改动：不需要重新编译 Swift 组件，也不用下载源码包。**

修的是什么：**外接屏不跟着内屏更新**（合上盖子回家插上显示器，副屏还停在昨天/更早的图）。两个原因：

1. 判断"壁纸是否已更新"时只读**主屏**，所以只要主屏对，整点补检查就认为"已经是最新"，外屏错了不会被发现；
2. macOS 没把"屏幕配置变化"事件投递给后台 helper（日志里只有唤醒事件），所以插屏后没人去重设。

0.1.12 改成：①**逐屏检查**——任何一块屏不一致就自动修复；②常驻 watcher **每 30 秒轻量问一次所有屏幕**，插上屏 30 秒内自动补上。

---

## 1. 升级（整段粘贴）

```bash
pipx install --force https://github.com/PelikanM400/paperdrop/releases/download/v0.1.12/paperdrop-0.1.12-py3-none-any.whl
paperdrop --version
paperdrop install
```

**应该看到**

- `installed package paperdrop 0.1.12, installed using Python ...`
- `paperdrop 0.1.12`
- `installed the daily schedule (07:35, macos):` 后面跟着两个 plist 路径

**如果失败长什么样**：`curl: (56)`、`SSL_ERROR`、`No such file or directory` 之类 —— 多为网络问题，重试一次即可；`paperdrop --version` 仍是 0.1.11 说明第 1 条没成功，别继续往下走。

> `paperdrop install` 这一步不能省：它会把常驻 watcher 重新加载，新代码才会生效。

## 2. 立刻把外屏补上（不用等 30 秒）

```bash
paperdrop apply-cached
```

**应该看到**一行：`applied /Users/<你>/.cache/paperdrop/image-<哈希>.jpg`，两块屏立刻变成当天的图。

## 3. 验收

```bash
paperdrop status
"$HOME/Library/Application Support/PaperDrop/paperdrop-set" screens
tail -n 5 ~/.local/state/paperdrop/paperdrop.log
```

- [ ] `paperdrop --version` 显示 **0.1.12**
- [ ] `screens` 每一行结尾都是 `image-<哈希>.jpg`（**不再是** `current.jpg`）
- [ ] `status` 里每一行 `screen:` 的路径都等于上面的 `current:` 那一行
- [ ] 日志最后几行有：`watching for screen configuration changes (native events plus a poll every 30s)`
- [ ] **终极验收**：拔掉外接屏 → 再插上 → 什么都不做，**30 秒内**外屏自动变成当天的图
      （日志里会出现 `1 of 2 screens do not show the cached image; re-applying`）

## 4. 出问题就发这两份给我

```bash
tail -n 30 ~/Library/Logs/dev.paperdrop.watch.err.log
tail -n 30 ~/.local/state/paperdrop/paperdrop.log
```

## 附：想调或想关

`~/.config/paperdrop/config.json` 里加/改：

```json
{ "schedule": { "screen_poll_seconds": 30 } }
```

- `30` = 每 30 秒检查一次所有屏幕（默认）
- `0` = 关掉轮询，只保留系统事件（插屏可能仍不自动修）
- 改完跑 `paperdrop install` 重新加载 watcher
