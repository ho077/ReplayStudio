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

py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 播放引擎：将 libmpv-2.dll 放到 vendor\mpv\ 下（见「依赖」）
.venv\Scripts\python.exe run.py
```

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
| 系统集成 | 拖放打开、HKCU「打开方式」注册（可撤销） |

## 依赖

| 组件 | 位置 | 说明 |
| --- | --- | --- |
| Python 3.13 + PySide6 | `.venv/` | GUI 与播放页，见 `requirements.txt` |
| libmpv | `vendor/mpv/libmpv-2.dll` | 播放引擎，**需自行下载**（LGPL/GPL 双许可，见 `docs/architecture/decisions/decision-001.md`）。取 `mpv-dev` 的 x86_64 包，解压后把 `libmpv-2.dll` 放进该目录 |
| FFmpeg / ffprobe | 系统 PATH | 缩略图抽帧、测试样本生成 |
| OBS Studio 32.2.2 便携版 | `vendor/obs/` | 录屏后端，同样不进版本库；`tools/probe_obs.py` 会以 `--portable` 启动并验证 obs-websocket 监听 127.0.0.1:4455 |

## 测试与验证

```powershell
.venv\Scripts\python.exe tests\smoke_core.py        # 解析→索引→查询
.venv\Scripts\python.exe tools\smoke_gui.py         # 导航/筛选/缩略图
.venv\Scripts\python.exe tools\smoke_phase2.py      # 播放器功能 18 项
.venv\Scripts\python.exe tools\smoke_containers.py  # 容器兼容 + 损坏容错 9 项
.venv\Scripts\python.exe tools\smoke_playback.py    # 真实录像播放
```

测试样本由 `tools/make_phase2_sample.py` 用 FFmpeg 现场合成到 `tests/_media/`，
**不会读写用户任何原始录像**。

## 文档

- 架构与决策：`docs/architecture/`（`decision-001` 技术栈、`decision-002` 播放器模型）
- 开发日志：`docs/devlog/`（000 立项 / 001 媒体库 / 002 主题 / 003 通用播放器）
- 学习文档：`docs/learning/`（含每篇三个动手任务）
- 故障排查：`docs/troubleshooting/known-issues.md`
- 当前状态与路线：`docs/project-status.md`、`docs/roadmap.md`

## 数据位置

`%LOCALAPPDATA%\ReplayStudio\`

```
index.db          # 索引 + 标签 + 观看历史 + 设置（可整库备份）
thumbs\           # 缩略图缓存（可安全删除，会重新生成）
screenshots\      # 播放截图
```

ACLOS 原始数据与视频文件**始终只读**，本程序不做任何删除、移动、转码。
