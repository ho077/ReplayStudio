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

### 第一个视频画面不在正中 / 画面相对黑边偏移（已修复，留档）
根因不是 mpv：带 `WA_NativeWindow` 的 `video_host`，其实 HWND 的位置是在**创建那一刻**
按当时几何定的。首个播放页在主窗口还没被窗口管理器摆好位置时创建，HWND 就偏了
（实测偏 (283,145) 物理像素），而布局之后只改尺寸、不纠正位置。
现在 `PlayerPage._sync_native_surface()` 在显示/缩放/引擎就绪后用 `SetWindowPos`
显式把 HWND 钉到 Qt 认为的矩形上。`smoke_tabs.py` 会断言 offset 必须为 (0,0)。

### 关闭应用时进程崩溃 / 用一会儿自己退出（已修复，留档）
两个独立的坑，都与"进程里存在多个 mpv 引擎"有关：

1. **退出时**：`os._exit()` 不够 —— `_exit()` 仍走 `ExitProcess`，会遍历各 DLL 的
   `DllMain(PROCESS_DETACH)`，libmpv 死在那里。改用 `TerminateProcess`
   （`diagnostics.hard_exit`，注意 ctypes 必须显式声明 argtypes，否则静默失败）。
2. **中途**：两个引擎**同时**各自持有真实音频输出 + 真实视频输出时会崩（实测 3/10）。
   现在后台标签被 park 时不仅暂停+静音，还释放音频设备（`aid=no`，回来恢复原轨道）。

判别方法：`audio=no` 不崩、`hwdec=no` 仍崩 → 就是音频侧。回归见 `tools/smoke_exit.py`。

### 声音有毛刺 / 「锯齿感」
**未最终定因**。已做：`audio-buffer` 0.2s → 0.4s、本地文件开 32 MiB demuxer 缓存、
避免多引擎同时占用音频设备。ACLOS 片段约 35 Mbit/s，而首次播放常与缩略图抽帧同时进行，
磁盘抖动导致的欠载是最常见来源。
若仍出现，请看 `logs/session-*.log` 里是否有 `underrun` 之类音频告警并反馈。

### 列表里出现「源文件缺失」的片段（已按用户要求默认隐藏，留档）
`file_state = 2` 的记录默认不出现在任何列表里（网格、最近播放、筛选计数、侧栏统计）。
记录仍保留在索引中，文件回来自动恢复显示。设置页 →「索引与缓存」→
「显示『源文件缺失』的片段」可切回旧行为；「仅缺失」筛选也仍然有效。

### 标签的关闭按钮溢出圆角（已修复，留档）
`QTabBar::close-button` 是**样式绘制**的子控件，几何不可靠，在圆角标签上必然溢出。
现在改为每标签 `setTabButton()` 放一个固定 18×18 的 `QToolButton#tabClose`，几何由 Qt
在标签矩形内摆放。顺带关闭了标签拖动排序（原按索引寻址，拖动后会显示错的播放器页面），
按钮绑定到 page 对象，新增 `Ctrl+W` 关闭当前标签。

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

