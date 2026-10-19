# ReplayStudio

Windows 11 本地视频管理 / 播放 / 录屏一体化桌面软件。

三个模块共用同一套播放引擎、索引库、缩略图系统、主题与设置中心：

- **无畏时刻** — 只读解析腾讯「无畏时刻」(ACLOS) 本地库，按地图/特工/模式/类型/胜负筛选回看
- **本地视频** — 任意文件夹浏览、播放列表、字幕音轨、断点续播
- **录屏工作台** — OBS + obs-websocket 集成（Phase 3 进行中，已完成 OBS 便携版与 4455 端口验证）

所有数据默认只留在本机，不上传任何视频或个人信息。

## 快速开始

需要 Windows 11 + Python 3.13。

```powershell
git clone https://github.com/ho077/ReplayStudio.git
cd ReplayStudio

# 1) 只跑一次：建 .venv、装依赖、检查 vendor\
setup.bat

# 2) 之后双击即可
start.bat
```

命令行等价写法（想自己控制时用）：

```powershell
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 播放引擎：将 libmpv-2.dll 放到 vendor\mpv\ 下（见「依赖」）
.venv\Scripts\python.exe run.py
```

> 上一行也可以换成双击 `debug.bat`：保留控制台、退出后不关窗口，
> 出问题时能直接看到 traceback 和退出码含义。

| 脚本 | 用途 |
| --- | --- |
| `setup.bat` | 环境准备，只跑一次：找 Python → 建 `.venv` → 装依赖 → 检查 `vendor\` |
| `start.bat` | 日常启动（`pythonw`，不留控制台窗口）；参数会转交给程序，可直接「打开方式」调用 |
| `debug.bat` | 调试启动（保留控制台，退出后暂停，解释退出码含义）。闪退、白屏时用它 |

> 三个 `.bat` 是 GBK + CRLF 编码（cmd 的要求），仓库里有 `.gitattributes` 保证换行不被转换。

首次启动会自动扫描本机「无畏时刻」库并后台建索引，中断后再次启动可续扫。

可选：把文件拖进窗口直接播放；或在设置页点「添加钮（打开方式）」后从资源管理器右键打开。

> `vendor/` 下的二进制不进版本库（体积大且需自行接受许可），因此 clone 后必须手动补齐 libmpv，否则播放页会报「播放引擎不可用」。

## 当前能力

| 模块 | 能力 |
| --- | --- |
| 媒体库 | ACLOS 只读解析、SQLite 增量索引、六维筛选、搜索、收藏、标签、观看历史、虚拟化网格、懒加载缩略图 |
| 播放 | libmpv 硬件解码（d3d11va）、倍速、全屏、画面比例、截图、断点续播、事件时间轴跳转、自动连播 |
| 字幕/音轨 | 内嵌轨选择、外挂 SRT/ASS、延迟微调、多音轨切换 |
| 主题 | 六套无畏契约特工配色，设置页即时切换并持久化 |
| 多标签播放 | 一个视频一个标签，关闭标签即销毁引擎；后台标签自动暂停并静音 |
| 崩溃取证 | 启动即写 `%LOCALAPPDATA%\ReplayStudio\logs\`，设置页可一键打开日志目录 |
| 系统集成 | 拖放打开、HKCU「打开方式」注册（可撤销）、`start.bat` / `debug.bat` 启动与取证入口 |

## 依赖

| 组件 | 位置 | 说明 |
| --- | --- | --- |
| Python 3.13 + PySide6 | `.venv/` | GUI 与播放页，见 `requirements.txt` |
| libmpv | `vendor/mpv/libmpv-2.dll` | 播放引擎，**需自行下载**（LGPL/GPL 双许可，见 `docs/architecture/decisions/decision-001.md`）。取 `mpv-dev` 的 x86_64 包，解压后把 `libmpv-2.dll` 放进该目录 |
| FFmpeg / ffprobe | 系统 PATH | 缩略图抽帧、测试样本生成 |
| OBS Studio 32.2.2 便携版 | `vendor/obs/` | 录屏后端，同样不进版本库；`tools/probe_obs.py` 会以 `--portable` 启动并验证 obs-websocket 监听 127.0.0.1:4455 |

## 测试与验证

```powershell
.venv\Scripts\python.exe tests\smoke_core.py            # 解析→索引→查询
.venv\Scripts\python.exe tools\smoke_gui.py             # 导航/筛选/缩略图
.venv\Scripts\python.exe tools\smoke_hidden_missing.py  # 缺失片段默认隐藏 11 项
.venv\Scripts\python.exe tools\smoke_diagnostics.py     # 崩溃日志捕获 3 项
.venv\Scripts\python.exe tools\smoke_tabs.py            # 标签/画面居中/关闭按钮 25 项
.venv\Scripts\python.exe tools\smoke_exit.py            # 关闭应用不崩溃 2 项
.venv\Scripts\python.exe tools\smoke_phase2.py          # 播放器功能 19 项
.venv\Scripts\python.exe tools\smoke_containers.py      # 容器兼容 + 损坏容错 9 项
.venv\Scripts\python.exe tools\smoke_playback.py        # 真实录像播放
```

测试样本由 `tools/make_phase2_sample.py` 用 FFmpeg 现场合成到 `tests/_media/`，
**不会读写用户任何原始录像**。

## 出问题了怎么排查

崩溃与运行日志在 `%LOCALAPPDATA%\ReplayStudio\logs\`（设置页有「打开日志目录」按钮）：

- `session-*.log` — 生命周期记录 + 未捕获异常的完整 traceback + Qt 告警
- `crash-*.log` — 原生崩溃（段错误 / 访问违例）的全线程栈，存在即说明是引擎层崩溃

日志 7 天后自动清理。反馈问题时附上最新的 `session-*.log` 即可。

## 文档

- 架构与决策：`docs/architecture/`（`decision-001` 技术栈、`decision-002` 播放器模型）
- 开发日志：`docs/devlog/`（000 立项 / 001 媒体库 / 002 主题 / 003 通用播放器 / 004 播放生命周期与崩溃日志 / 005 画面居中、隐藏已删片段与崩溃定因）
- 学习文档：`docs/learning/`（含每篇三个动手任务）
- 故障排查：`docs/troubleshooting/known-issues.md`
- 当前状态与路线：`docs/project-status.md`、`docs/roadmap.md`
- 打包成 exe 的方案：`docs/packaging.md`（**方案已定、尚未执行**，等 Phase 3 完成再打包）

## 数据位置

`%LOCALAPPDATA%\ReplayStudio\`

```
index.db          # 索引 + 标签 + 观看历史 + 设置（可整库备份）
thumbs\           # 缩略图缓存（可安全删除，会重新生成）
screenshots\      # 播放截图
```

ACLOS 原始数据与视频文件**始终只读**，本程序不做任何删除、移动、转码。
