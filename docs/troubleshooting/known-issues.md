# 已知问题与故障排查

## 环境类

### `RuntimeError: 未找到 libmpv-2.dll`
确认 `vendor/mpv/libmpv-2.dll` 存在（115 MB）。python-mpv 通过 `%PATH%` 找它，
`media/player.py::ensure_mpv_dll` 已自动处理；若手动跑脚本，先 `import replaystudio.media.player`。

### GitHub 直连超时
本机走代理 `http://127.0.0.1:7897`：`git -c http.proxy=... clone ...`。

## 数据类

### 显示"源文件缺失"
435 条记录对应的 mp4 已被腾讯客户端清理。数据真实状态。可在设置页"重建索引"后
配合网格右键或"仅缺失文件"筛选处理；本项目绝不主动删除任何源文件。

### 特工/地图显示为英文 ID
中文名映射来自官方本地化表；未收录的新特工/新地图会回退显示原始名称（刻意不编造）。
更新 `aclos/taxonomy.py` 后重建索引即可。

## 进程/生命周期类

### 返回媒体库后还在响声音（已修复，留档）
根因：`navigate()` 只 `setCurrentWidget`，播放页被**隐藏**而非停止，`MpvPlayer` 继续解码出声。
现在播放由 `ui/player_tabs.py` 承载「一视频一标签」：关闭标签/按返回会
`release()` 销毁引擎；切到别处 `park_all()` 会**暂停并静音**全部标签。
详见 `docs/devlog/004-playback-lifecycle-and-crash-logging.md`。

### 程序静默退出 / 崩溃
现在启动即写日志，位置在 `%LOCALAPPDATA%\ReplayStudio\logs\`：

| 文件 | 内容 |
| --- | --- |
| `session-*.log` | 生命周期面包屑（开关标签、播放路径）+ 未捕获异常的完整 traceback + Qt 告警 |
| `crash-*.log` | `faulthandler` 的全线程原生栈（段错误 / 访问违例） |

设置页 →「索引与缓存」→「打开日志目录」。反馈崩溃时请附**最新的 `session-*.log`**；
如果同时存在 `crash-*.log` 则说明是原生崩溃（多半在 libmpv 内部）。
7 天前的日志会在启动时自动清理。

已消除的两个已知诱因：
1. `Signal source has been deleted` —— mpv 事件线程向已销毁的 Qt 对象发信号，
   抛在事件循环之外会直接杀进程。现所有引擎回调经 `MpvPlayer._guard()` 包裹，
   且 `shutdown()` 先降 `_alive` 旗再 `stop` 再 `terminate`。
2. 向**隐藏**的原生子窗口持续渲染（d3d11va）。标签模型下不再存在隐藏的活跃播放页。

### 标签开太多变慢
上限 6 个（`ui/player_tabs.py::MAX_TABS`），超出会关闭最早的非当前标签。
每个标签持有独立 `MpvPlayer`，也就是一份独立解码器。

## 播放类

### 续播报 mpv -12（已修复，留档）
`loadfile` 后立刻 `seek` 有竞态；现用 `start` 选项。若再遇 -12，检查是否在
file-loaded 之前就发命令。

### 自动连播不触发 / 跳过一部 / 卡在第一帧（已修复，留档）
三个坑叠在一起，都在 `media/player.py`：

1. `keep_open=always` 下播完**不发 `end-file`**，要用 `eof-reached` 属性判断结束。
2. 但 `end-file`(reason=eof) 在某些路径也会发 → 两条通道都 emit `ended` → 连播跳过一个。
   现在用 `_ended_fired` 闩锁（每次 `play()` / `file-loaded` 复位）。
3. `keep_open` 会在片尾把 `pause` 置 true 并**被下一个 loadfile 继承** →
   下一部视频加载后停在第一帧。`play()` 里显式 `self._mpv.pause = False`。

### 截图工具抓不到视频画面
mpv 渲染在原生子窗口（`wid`），`win.grab()` 抓不到其内容——播放本身正常，
看时间轴走动即可确认。文档截图里的黑色视频区就是这个原因。

### 「打开方式」里没有 ReplayStudio
设置页点一次「添加钮（打开方式）」。它只写 `HKCU\Software\Classes`，
若之前被安全软件清理过需要重新注册；点「移除」可完全撤销。

## Qt/PySide6 类（踩坑留档）

- `QImage.scaled()` 关键字参数是 `aspectMode`，**没有** `aspectRatioMode`；
  传错会在后台线程抛 AttributeError 被吞掉。一律用枚举：
  `Qt.AspectRatioMode.KeepAspectRatio`。
- `QPainter` 没有 `setClipped()`，是 `setClipping(False)`。
- **QSS 的 8 位十六进制是 `#AARRGGBB`（alpha 在前）**。写 Web 风格 `#RRGGBBAA`
  会被解析成一个不透明且色相错误的颜色（曾经的"选中项显示成橄榄绿"就是这个）。
- **给信号 `connect` 一个裸 lambda 可能被 GC 回收**，槽静默失效、时灵时不灵。
  测试或长生命周期连接要持有函数引用。
- `QMenu.exec()` 是阻塞的。要自动化截图/测试菜单，把"构建菜单"和"弹出菜单"拆成两个方法，
  单独 `menu.ensurePolished(); menu.adjustSize(); menu.grab()`。

