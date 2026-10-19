# 项目状态

更新：2026-10-19（Phase 1 + 主题系统 + Phase 2 通用播放器 + 播放生命周期重构与崩溃日志）

## ✅ 已完成并通过验证

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

## ⚠️ 已知问题与风险

- **用户报告的那次崩溃尚未 100% 定因**：两条高度可疑路径（`Signal source has been deleted`、
  向隐藏窗口持续 d3d11va 渲染）都已消除，但当时没有日志。现已具备取证能力，
  若复现请提供 `%LOCALAPPDATA%\ReplayStudio\logs\` 下最新的 `session-*.log` / `crash-*.log`
- 435 个片段源文件已被官方客户端清理，显示"源文件缺失"（数据真实状态，非 Bug）
- mpv 渲染在原生子窗口，`win.grab()` 抓不到画面（正常播放不受影响，截图里视频区是黑的）
- 播放上下文是"打开时的快照"：播放中切筛选不会影响已在播的队列（设计如此，见 ADR-002）
- 标签数上限 6（`MAX_TABS`）是经验值，未做显存压力测试
- 录屏依赖外部 OBS 进程：OBS 关闭/崩溃时录制会中断，Phase 3 需要做状态同步与重连提示
- OBS 便携模式下的 NVENC/QSV 等编码器需在实际录制时逐个探测可用性

## 💡 本阶段最值得学习的三个技术点

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

```powershell
cd D:\WorkSpace\WorkBuddySpace\apps\ReplayStudio
.venv\Scripts\python.exe run.py
```

依赖：`vendor/mpv/libmpv-2.dll`（已就位）、FFmpeg（PATH 中，Scoop）。
数据目录：`%LOCALAPPDATA%\ReplayStudio\`（索引库 + 缩略图缓存 + 截图 + `logs\` 日志，可整目录备份）。

## 🧪 如何验证

```powershell
.venv\Scripts\python.exe tests\smoke_core.py        # 解析→索引→查询
.venv\Scripts\python.exe tools\smoke_gui.py         # 导航/筛选/缩略图
.venv\Scripts\python.exe tools\smoke_tabs.py        # 标签生命周期（串音/崩溃回归）21 项
.venv\Scripts\python.exe tools\smoke_diagnostics.py # 崩溃日志捕获能力 3 项
.venv\Scripts\python.exe tools\smoke_phase2.py      # 播放器全部功能 19 项
.venv\Scripts\python.exe tools\smoke_containers.py  # 容器兼容 + 损坏容错 9 项
.venv\Scripts\python.exe tools\smoke_playback.py    # 真实录像播放
```
