# 项目状态

更新：2026-10-19（Phase 0 + Phase 1 完成度约 90%）

## ✅ 已完成并通过验证

- ACLOS 加密数据库只读解析（3 账号 / 605 对局 / 1471 视频 / 7944 事件）
- SQLite 索引库 + 后台增量扫描（0.89 s 全量建索引，不阻塞 UI）
- 深色主题主界面：无畏时刻六维筛选 / 搜索 / 收藏 / 虚拟化网格 / 缩略图懒加载
- libmpv 播放器：硬解 d3d11va、倍速、音量、全屏、事件时间轴点击跳转、断点续播
- 本地视频浏览页（复用同一套网格与播放器）
- 设置页（数据源、硬解、索引重建、缓存清理）
- 端到端验证 + 三页真实截图（`docs/screenshots/`）

## 🟡 已实现但尚未充分验证

- 大目录（>10 万文件）扫描性能
- 4K / HEVC / 外挂字幕（SRT/ASS）实际播放
- 多显示器 / 高 DPI 布局
- 播放历史"最近播放"页在长历史下的表现

## 🔜 正在进行 / 下一阶段

- **Phase 2**：通用播放器补全（播放列表、字幕音轨 UI、文件关联、拖放打开）
- **Phase 3**：录屏（前置：安装 OBS Studio；方案 obs-websocket，见 ADR-001）

## ⚠️ 已知问题与风险

- 435 个片段源文件已被官方客户端清理，显示"源文件缺失"（数据真实状态，非 Bug）
- mpv 渲染在原生子窗口，窗口截图工具抓不到画面（正常播放不受影响）
- OBS 未安装，录屏模块无法开始

## 💡 本阶段最值得学习的三个技术点

1. AES-256-CBC 密钥派生（`aclos/crypto.py`，10 行代码读懂对称加密流程）
2. 跨线程 Qt 信号纪律（`media/player.py` 的"回调只 emit"原则）
3. Qt model/view 虚拟化（`ui/grid.py`，1471 项与 24 项同开销的原理）

## ▶ 如何运行

```powershell
cd D:\WorkSpace\WorkBuddySpace\apps\ReplayStudio
.venv\Scripts\python.exe run.py
```

依赖：`vendor/mpv/libmpv-2.dll`（已就位）、FFmpeg（PATH 中，Scoop）。
数据目录：`%LOCALAPPDATA%\ReplayStudio\`（索引库 + 缩略图缓存，可整目录备份）。
