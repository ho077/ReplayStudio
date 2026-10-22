# 项目状态

更新：2026-10-23（Phase 1 + 主题系统 + Phase 2 通用播放器 + 播放生命周期重构 + 三至六轮稳定性修复 + 全屏与分类计数 + 第七轮单实例与缩略图管线加固）

## ✅ 已完成并通过验证

### 第六轮：全屏 + 分类计数（2026-10-22）
- **全屏之前是个空操作**：mpv 的 `fullscreen` 属性管的是它自己创建的窗口，嵌入模式
  （`wid=`）下不存在。`probe_fullscreen.py mpv` 实测四次开关，宿主 HWND 矩形、
  `GWL_STYLE`、窗口状态全部不动 —— 按 F 什么都没发生。现改走
  `QMainWindow.showFullScreen()`，收起标题栏/侧边栏/状态栏、隐藏播放页信息面板与
  标签条；关闭标签或离开播放页会自动落回窗口状态
- **补上"双击画面"**：按钮 tooltip 一直写着「F / 双击画面」，但双击从来没绑过
  （画面是 native window，用事件过滤器监听）
- **Esc 语义分层**：全屏时先退出全屏，再按才关闭标签
- **分类括号里的数字在撒谎**：`facets()` 不含搜索框 / 仅收藏 / 仅我的击杀 / 胜负等
  条件，网格套的却是完整筛选 →「霓虹町 (154)」点下去只有 153 条。现在 WHERE 构建统一
  走 `Database._build_where(f, skip)`，每个维度按"除自己以外的全部条件"计数，且每次
  `reload()` 原地刷新标签（不重建下拉框，避免弄丢选中项）
- **验证**：`probe_facet_counts.py` 修复前 7 处不符 → 0 处；`probe_facet_ui.py` 真实
  MomentsPage 16 项 `label == grid == shown`；分页 `三杀时刻 (686)` 滚到底
  `loaded == promised == 686`；`probe_fullscreen.py qt 8` 与 `qt-stress` 3 轮全过
- 新增探针：`probe_fullscreen.py`、`probe_facet_counts.py`、`probe_facet_ui.py`；
  过程记录：`docs/devlog/008-fullscreen-and-facet-counts.md`

### 第五轮：切主题崩溃 + 页面拆除（2026-10-21 ~ 22）
- **用户报的「切几遍颜色就崩」已修复并复现验证**：关掉一个标签后再切主题，
  必然在 `QApplication::setStyleSheet()` 内部访问违规（`probe_theme_leak.py 1 12` → 139）。
  逐项关掉拆除步骤定位到元凶是"断开整棵子控件树的信号"，它连 Qt 在组合控件内部的
  连接一起拆了。现在只断页面自身的信号，子控件整个销毁（`ui/qt_teardown.py`）
- **拆穿一个长期假象**：`QObject.disconnect()` 零参数形式在 PySide6 里**根本不存在**，
  抛 `TypeError` 被 `except TypeError` 吞掉 —— 原来的清理代码一次都没生效。
  零参数形式只有 `SignalInstance`（`btn.clicked`）支持
- **另一个假象是所有探针的"事件循环"**：`app.processEvents()` 循环不投递 DeferredDelete，
  `deleteLater()` 永不生效，"页面没被释放"是测量误差。改用 `QEventLoop` 后全部归零，
  并用 `shiboken6.isValid()` 判定
- **结果**：关闭 4 个标签后页面残留 0/4；关标签后连切 12 次主题、开着视频连切 12 次主题、
  `probe_theme_crash.py` 20 轮，全部 EXIT=0
- **回归套件全绿**：`smoke_core` / `smoke_tabs` 25/25 / `smoke_playback` /
  `smoke_containers` / `smoke_hidden_missing` / `smoke_gui` / `smoke_phase2` 19/19 /
  `smoke_exit` 2/2 / `probe_mpv_lifecycle` 无残留线程
- 新增探针：`probe_disconnect.py`、`probe_page_refs.py`、`probe_qss_crash.py`、
  `probe_crash_bisect.py`；过程记录：`docs/devlog/007-*.md`

### 第四轮：启动脚本 + 打包方案（2026-10-20）
- **`start.bat` / `debug.bat` / `setup.bat`**：双击启动、调试入口、一次性环境准备。
  日常用 `pythonw` 不留控制台；`debug.bat` 保留窗口并解释退出码（`0xC0000005` 在 cmd 里
  显示为 `-1073741819`，已按实测写进提示）
- **三个脚本都用临时目录 + 桩程序逐条验证过**：正常启动无输出、瞬间退出会提示、
  缺 `.venv` 会指路 `setup.bat`、参数（含空格路径）完整传递
- **`setup.bat` 能在没有 `python` 的机器上工作**：本机 PATH 里既没有 `python` 也没有 `py`，
  脚本按 PATH → 启动器 → 常见安装位置 → 手动输入 的顺序找，实测命中 `D:\Anaconda3\python.exe`
- **`.bat` 一律 GBK + CRLF**，并加 `.gitattributes` 锁住换行
- **打包方案落文档**（`docs/packaging.md`）：PyInstaller onedir、先换 PySide6-Essentials、
  OBS 做成可选、GPL/LGPL 随包许可证清单。**尚未执行打包**，等 Phase 3 完成
- 过程记录：`docs/devlog/006-launcher-scripts-and-packaging-plan.md`

### 第三轮：体验与稳定性修复（用户截图反馈驱动）
- **首帧画面居中**：定位到 `video_host` 的原生 HWND 在创建时被定歪（实测偏 (283,145) px），
  布局只改尺寸不纠位置。新增 `_sync_native_surface()` 用 `SetWindowPos` 钉住，
  抓屏验证上下黑边 102/102 对称、水平偏差 0
- **隐藏已删除的片段**：`Database.show_missing` 默认 False，`query_videos` / `recent` /
  `facets` / `stats` 全部按可见口径；索引仍保留记录，文件回来自动恢复；
  设置页可切回（11/11 测试）
- **标签关闭按钮**：弃用不可控的 `QTabBar::close-button`，改自绘 18×18 `QToolButton`，
  结构上不可能越界；同时修掉"拖动标签后按索引寻址会显示错页面"的隐患，补 `Ctrl+W`
- **两个必崩缺陷（用户报的「自己退出了」）**：
  1. 退出时 libmpv 在 DLL detach 阶段崩溃 → `diagnostics.hard_exit()` 用
     `TerminateProcess` 跳过 detach 遍历
  2. 两个引擎同时持有真实音频+视频输出时中途崩溃（3/10）→ 后台标签 park 时
     释放音频设备（`aid=no`，回来恢复轨道）
  修复前 5/5 崩溃，修复后连续 12/12 全过程干净退出
- **音频**：`audio-buffer` 0.2s→0.4s + 本地 32 MiB demuxer 缓存（缓解欠载型毛刺）；
  mpv 的 error 与音频告警开始写入 session 日志，便于下次取证
- 截图证据：`docs/screenshots/phase3/`；过程记录：`docs/devlog/005-*.md`

### 播放生命周期重构（21/21 + 3/3 自动化验证）— 修复用户反馈的两个缺陷
- **一视频一标签**：`ui/player_tabs.py`，每个标签持有独立 `PlayerPage` + 独立 `MpvPlayer`；
  关闭标签/按「返回」→ `release()` 销毁引擎并释放原生 surface
- **后台串音修复**：切走标签 → 暂停 **且** 静音（保留位置）；离开整个播放区 → `park_all()`；
  回到播放区 → 恢复原播放状态。这是「返回后后台还有声音」的根治
- **后台标签不再驱动全局动作**：`_relay()` 只转发当前标签的信号，修掉连播跳一条
- **侧边栏「▶ 正在播放」**：随时回到还开着的标签，不再出现"标签是孤儿"
- **崩溃日志**：`core/diagnostics.py`，启动即装 `faulthandler`（原生崩溃/全线程栈）+
  `sys.excepthook`（未捕获异常）+ Qt 消息处理器，落盘 `%LOCALAPPDATA%\ReplayStudio\logs\`，
  7 天自动轮转；设置页新增「打开日志目录」
- **引擎回调加固**：11 个 observer + event callback 全部经 `_guard()`，消除
  `Signal source has been deleted` 这类抛在事件循环之外、会直接杀进程的异常
- 截图证据：`docs/screenshots/phase3/`；详细过程：`docs/devlog/004-*.md`

### Phase 1 — 无畏时刻库
- ACLOS 加密数据库只读解析（3 账号 / 605 对局 / 1471 视频 / 7944 事件）
- SQLite 索引库 + 后台增量扫描（0.89 s 全量建索引，不阻塞 UI）
- 深色主题主界面：无畏时刻六维筛选 / 搜索 / 收藏 / 虚拟化网格 / 缩略图懒加载
- libmpv 播放器：硬解 d3d11va、倍速、音量、全屏、事件时间轴点击跳转、断点续播
- 本地视频浏览页（复用同一套网格与播放器）
- **六套特工主题**（无畏契约/捷影/炼狱/蕾娜/贤者/零），设置页可视化切换、
  即时生效、持久化（`docs/screenshots/themes/`）

### Phase 2 — 通用播放器（19/19 + 9/9 自动化验证）
- **播放列表**：播放页右侧「列表」页签，显示当前视图快照，可点击切换
- **自动连播**：播完自动下一项（可在设置关闭），到头自动停
- **字幕**：内嵌轨选择 / 开关、外挂 SRT/ASS 加载、提前·延后 0.5s、延迟记忆
- **音轨**：多音轨列出并切换（显示标题 / 语言 / 声道 / 编码）
- **画面比例**：自动 / 16:9 / 4:3 / 21:9 / 1:1 / 2.35:1 / 拉伸
- **截图**：一键存 PNG 到 `%LOCALAPPDATA%\ReplayStudio\screenshots`
- **拖放打开**：文件/文件夹拖到窗口即可播放
- **文件关联**：「打开方式」注册到 HKCU，可一键撤销，不抢系统默认播放器
- **快捷键**：Space / ←→（Shift 长跳）/ ↑↓ 音量 / F 全屏 / [ ] 倍速 / S 截图 / N P 上下条
- 截图证据：`docs/screenshots/phase2/`

## 🟡 已实现但尚未充分验证

- 大目录（>10 万文件）扫描性能
- 4K / HEVC / HDR 片源实际播放
- ASS 复杂特效（定位、卡拉 OK）逐项比对
- 非 UTF-8（GBK）字幕文件
- 多显示器全屏、高 DPI 布局
- 从资源管理器双击触发「打开方式」的系统级行为（HKCU 写入本身已用注册表读取验证）

## 🔜 正在进行 / 下一阶段

- **Phase 3**：录屏工作台
  - [x] **前置条件已解除**：OBS 32.2.2 便携版已解压到 `vendor/obs/`（免安装、免管理员权限、
    不写系统目录、不影响用户已有 OBS 配置），`tools/probe_obs.py` 实测启动后
    `obs-websocket` 在 `127.0.0.1:4455` 正常监听。
  - [ ] WebSocket 握手与认证（Identify）、事件订阅
  - [ ] 录制模式 / 参数预设 / 编码器能力探测
  - [ ] 全局热键 + 托盘 + 成品自动入库
- Phase 4：性能基准与体验打磨
- **Phase 5：打包 exe** —— 方案已写在 `docs/packaging.md`，
  触发条件是 Phase 3（录屏）完成。动手前先做三件事：
  `PySide6` → `PySide6-Essentials`、给 `ensure_mpv_dll()` 加 `sys._MEIPASS` 冻结分支、
  准备 `LICENSES/`（mpv 是 GPLv2+、OBS 是 GPLv2、Qt 是 LGPLv3）

## ⚠️ 已知问题与风险

- **「声音有锯齿感」尚未定因**：已把 `audio-buffer` 提到 0.4s、开本地 demuxer 缓存、
  并消除多引擎同时占用音频设备；这三项都是有证据的缓解手段，但没有直接复现验证。
  若仍出现，`logs/session-*.log` 里现在会记录 `underrun` 类音频告警
- **崩溃的根因已修但过程值得记一笔**：libmpv 在 DLL detach 阶段与 `TerminateProcess`
  规避，本质是"绕开"而非"修好上游"。若换 libmpv 版本，`tools/smoke_exit.py` 要重跑
- 435 个片段源文件已被删除（用户主动删的），默认不再陈列；索引保留以便文件回来自动恢复
- mpv 渲染在原生子窗口，`win.grab()` 抓不到画面（正常播放不受影响，截图里视频区是黑的）
- 播放上下文是"打开时的快照"：播放中切筛选不会影响已在播的队列（设计如此，见 ADR-002）
- 标签数上限 6（`MAX_TABS`）是经验值，未做显存压力测试
- 录屏依赖外部 OBS 进程：OBS 关闭/崩溃时录制会中断，Phase 3 需要做状态同步与重连提示
- OBS 便携模式下的 NVENC/QSV 等编码器需在实际录制时逐个探测可用性

## 💡 本阶段最值得学习的三个技术点

1. **"Qt 认为的位置"≠"原生窗口的位置"**（`player_page.py::_sync_native_surface`）：
   带 `WA_NativeWindow` 的 QWidget 会在创建那一刻把 HWND 定在当时的几何上，
   之后布局只改尺寸。首次打开偏位、切一下又正常，就是这个时间差的指纹。
2. **`os._exit()` 并不等于"不跑 teardown"**（`diagnostics.hard_exit`）：
   `_exit()` 仍走 `ExitProcess` → 遍历各 DLL 的 `DllMain(PROCESS_DETACH)`。
   要真正跳过必须 `TerminateProcess`，且 ctypes 不声明 argtypes 会静默失败 —— 很坑。
3. **"清理缺失记录"要清理三处口径**（`core/db.py`）：列表、筛选计数、统计。
   只改列表会出现"筛选写着 12 条、网格只有 9 条"这种自相矛盾的界面。

### 上一轮（播放生命周期）的三个技术点

1. **"页面隐藏"不等于"播放停止"**（`ui/player_tabs.py`）：把播放从"页面状态"提升为
   "会话对象"，一个标签即一个会话。串音和可能的崩溃都是这个抽象缺失的连坐后果。
2. **Qt 之外抛出的异常会直接杀进程**（`media/player.py::_guard`）：mpv 事件线程向已销毁的
   Qt 对象 emit 信号产生的 `RuntimeError`，不经过 Qt 异常处理，也没有任何 try 能接住。
   所有跨线程回调都必须自带存活检查 + 全捕获。
3. **崩溃取证要在第一行安装**（`core/diagnostics.py`）：窗口模式没有控制台，
   `faulthandler` 必须在 `QApplication` 之前 enable，否则最早期的原生崩溃依然无痕。

### 上一阶段（Phase 2）的三个技术点，仍然成立
1. **`eof-reached` 属性 vs `end-file` 事件**（`media/player.py::_on_eof`）：
   为什么"播完"在 mpv 里不是一件理所当然的事，以及为什么要加去重闩锁。
2. **引擎状态继承**（`player.py::play` 里的 `self._mpv.pause = False`）：
   一个"下一条视频卡在第一帧"的 Bug，根因在上一部视频留下的状态。
3. **菜单 build/show 分离**（`player_page.py::_build_sub_menu`）：
   把阻塞的 `exec()` 从构建逻辑里剥离，换来可测试、可截图、可复用的 UI 代码。

## ▶ 如何运行

双击 `start.bat` 即可（出问题改双击 `debug.bat`）。命令行等价写法：

```powershell
cd D:\WorkSpace\WorkBuddySpace\apps\ReplayStudio
.venv\Scripts\python.exe run.py
```

依赖：`vendor/mpv/libmpv-2.dll`（已就位）、FFmpeg（PATH 中，Scoop）。
数据目录：`%LOCALAPPDATA%\ReplayStudio\`（索引库 + 缩略图缓存 + 截图 + `logs\` 日志，可整目录备份）。

## 🧪 如何验证

```powershell
.venv\Scripts\python.exe tests\smoke_core.py            # 解析→索引→查询
.venv\Scripts\python.exe tools\smoke_gui.py             # 导航/筛选/缩略图
.venv\Scripts\python.exe tools\smoke_hidden_missing.py  # 缺失片段默认隐藏 11 项
.venv\Scripts\python.exe tools\smoke_diagnostics.py     # 崩溃日志捕获能力 3 项
.venv\Scripts\python.exe tools\smoke_tabs.py            # 标签生命周期/画面居中/关闭按钮 25 项
.venv\Scripts\python.exe tools\smoke_exit.py            # 关闭应用不崩溃（真实 app.main）2 项
.venv\Scripts\python.exe tools\smoke_phase2.py          # 播放器全部功能 19 项
.venv\Scripts\python.exe tools\smoke_containers.py      # 容器兼容 + 损坏容错 9 项
.venv\Scripts\python.exe tools\smoke_playback.py        # 真实录像播放
```

所有脚本都走与正式程序一致的退出路径，**退出码 0 本身也是断言**（此前退出即段错误）。

定位问题时用的探针（不是回归测试，按需手动跑）：

```powershell
.venv\Scripts\python.exe tools\probe_video_rect.py     # mpv 窗口 vs Qt 控件几何
.venv\Scripts\python.exe tools\probe_video_center.py   # 抓屏量画面黑边是否对称
.venv\Scripts\python.exe tools\probe_audio.py          # 音频后端/缓冲/欠载
```
