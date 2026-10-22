# 已知问题与故障排查

## 环境类

### 软件卡住 / 界面全部「载入封面…」（2026-10-23 已修复）
历史原因：允许多实例并存，互相争数据库写锁（原 30s 忙等）与缩略图缓存
（并发写坏 JPEG 后永久毒化）。现已：单实例锁（第二实例把文件转交给正在
运行的窗口后退出）、缓存原子写 + 坏文件自动清理、失败缩略图本会话不重试、
DB 忙等降到 3s。详见 devlog 009。若再次遇到，先查任务管理器是否有多个
python.exe 残留。

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

### 按 F 全屏没反应（已修复，留档）
原因不是崩溃，是**空操作**：mpv 的 `fullscreen` 属性管的是它自己创建的窗口，而嵌入
模式（`wid=`）下那个窗口不存在。`tools/probe_fullscreen.py mpv` 实测四次开关，宿主
HWND 的矩形、`GWL_STYLE`、窗口状态全部纹丝不动。现改走 `QMainWindow.showFullScreen()`
（并收起标题栏/侧边栏/状态栏、隐藏播放页信息面板与标签条）。Esc 在全屏时先退出全屏。

### 分类下拉框括号里的数字比实际多（已修复，留档）
`facets()` 原来只按账号统计，不含搜索框 / 仅收藏 / 仅我的击杀 / 胜负等条件，而网格
查询套的是完整筛选，于是"霓虹町 (154)"点下去只有 153 条。现在 WHERE 构建统一走
`Database._build_where(f, skip)`，每个维度用"除自己以外的全部条件"计数，数字随其他
筛选联动（分面搜索的标准行为）。详见 devlog 008。

## 启动脚本 / 批处理类（踩坑留档）

### 双击 `start.bat` 没有任何反应
按这个顺序查：
1. `setup.bat` 跑过没有（`.venv\Scripts\pythonw.exe` 在不在）
2. `debug.bat` 里看真实报错 —— `start.bat` 用的是 `pythonw`，报错不会显示
3. `%LOCALAPPDATA%\ReplayStudio\logs\` 下最新的 `session-*.log`

`start.bat` 只在"进程起来后 1 秒内又没了"时才提示，慢一点的崩溃它抓不到，
这种情况一律上 `debug.bat`。

### 崩溃退出码在 cmd 里是负数
`0xC0000005`（访问违例）在批处理里显示成 `-1073741819`，不是 `3221225477`。
`debug.bat` 的提示按负数写的。对应关系：`-1073741819 = 0xC0000005`，
`-1073740791 = 0xC0000409`（栈溢出 / 快速失败）。

### 改 `.bat` 后中文变乱码
`.bat` 必须是 **GBK + CRLF**：中文 Windows 的 cmd 用 OEM 代码页 936 逐行读文件，
UTF-8 会乱码；而且编辑工具往 GBK 文件里插中文可能写成 UTF-8 字节，
文件立刻变混合编码、连解码都失败。改完用 `python -c "...".decode('gbk')` 验一遍。
仓库的 `.gitattributes` 只保证换行，不保证编码。

### 批处理自身的两个陷阱（写脚本时会再踩）
- **`del` 会把 `%ERRORLEVEL%` 清零**：`fc` 之后先 `set "SAME=%ERRORLEVEL%"` 再 `del`，
  否则 `if not errorlevel 1` 恒真。
- **`for /f in ('"C:\path with space\exe" -c "..."')` 会失败**：
  `cmd /c` 会吃掉首尾引号，报 `'C:\path with space\exe" -c "import' 不是内部或外部命令`。
  改成先把输出重定向到临时文件，再 `set /p VAR=<file` 读回来。

### 切换主题时崩溃 / 界面点不动（已修复，留档）
关掉标签后切配色必崩，落在 `QApplication::setStyleSheet()` 内部（`faulthandler` 的
`crash-*.log` 可直接看到）。根因是 `PlayerPage.release()` 里"断开整棵子控件树的信号"：
那会连 Qt 建在组合控件内部（滚动区、列表视图）的连接一起断掉，下一次换样式表就踩野指针。
现在只断**页面自身**的信号，子控件改为整个销毁 —— 连接活不过它的发送者，效果一样且安全。
详见 `docs/devlog/007`；回归用 `tools/probe_crash_bisect.py`（含"没有 mpv 也照样崩"的对照）。

### 界面偶发卡死 / 「点不动」（已修复，留档）
`mpv.<prop> = v` 是**同步往返**（`mpv_set_property`）。pause / mute / volume /
park 时的 `aid=no` 全都从 GUI 线程发出，引擎忙时调用方就卡在 libmpv 里。
现在统一走 `MpvPlayer._write()` → `command_async("set", …)`，投递即返回。
详见 `docs/devlog/007` 第 5.1 节。

### 日志里反复出现 `0xe24c4a02` / `<invalid frame>`（不是崩溃原因）
这是 **LuaJIT 用"抛 SEH 异常再自己接住"来展开栈**，libmpv 内部常态发生，本身无害。
`--load-scripts=no` 实测关不掉它。危险的是 `faulthandler.enable(all_threads=True)`：
它的 VEH 排在 LuaJIT 前面，每次都要遍历**所有线程**的栈，而那个线程正被 LuaJIT 改写
栈指针 —— dump 出现 `<invalid frame>` 之后往往就是一次真实的访问违规。
现在 `diagnostics.install()` 用 `all_threads=False`，只 dump 出错的那一个线程。
**排查时不要把这个码当成根因。**

## Qt/PySide6 类（踩坑留档）

- **`QObject.disconnect()` 零参数形式不存在**：调用抛
  `TypeError: not enough arguments`，非常容易被 `except TypeError` 吞掉，
  于是清理代码看起来在跑、实际一次没生效。零参数形式只在 **`SignalInstance`**
  （`btn.clicked`）上有。见 `ui/qt_teardown.py::disconnect_object`。
- **不要断开整个控件子树的信号**（见上一条崩溃）：会拆掉 Qt 内部连接。
  要断开就断自己这一个，其余靠销毁。
- **测生命周期必须用真实事件循环**。`app.processEvents()` 循环**不投递 DeferredDelete**，
  于是 `deleteLater()` 永远不生效，任何"控件没被释放"的结论都是假的。用
  `QEventLoop` + `QTimer.singleShot(ms, loop.quit)` + `loop.exec()`。
  判定用 `shiboken6.isValid(obj)`，比"weakref 还在不在"准确得多。
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

