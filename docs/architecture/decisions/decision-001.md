# 决策 001：技术栈选型 —— Python + PySide6 + libmpv + SQLite

- 状态：已接受（2026-10-19）
- 决策人：ReplayStudio 开发会话

## 背景与问题

目标软件需要：管理数百 GB 的无畏契约「无畏时刻」录像（ACLOS 数据）、通用本地视频播放、
未来的录屏集成。候选架构至少两个：

| 方案 | 组成 | 优势 | 劣势 |
| --- | --- | --- | --- |
| A | Tauri 2 + Rust + React + libmpv | 安装包小、Web UI 灵活 | 本机无 Rust 工具链；libmpv 嵌入 WebGL/渲染层调试成本高；视频渲染与窗口合成是高风险点 |
| B | Python + PySide6 + libmpv (python-mpv) | 本机 Python 环境成熟；libmpv 原生 `wid` 嵌入 Qt 简单可靠；迭代快 | 打包体积大；GIL 下的并发需要线程纪律 |

## 验证过程（Phase 0 三个高风险点）

1. **ACLOS 数据解密**：参考 WonderfulUI（GPL-3.0）的格式文档与解析逻辑，自行实现了
   hex 解码 + AES-256-CBC（密钥 = SHA256(openid)）的独立解析器 `src/replaystudio/aclos/`，
   用本机真实数据库验证：3 账号 / 605 对局 / 1471 视频 / 7944 事件全部解析成功。
2. **libmpv 嵌入 Qt 播放**：`tools/probe_mpv.py` 播放真实 1080p60 MP4，
   硬件解码 `d3d11va` 生效，seek 精确。Python 侧通过 `wid` 嵌入 QWidget，无闪烁。
3. **OBS 连接**：本机 OBS 未安装，Phase 3 前置条件，标记为未验证项。

## 决策

选择方案 B。理由：

- 三个高风险点中两个已实测通过，方案 A 的第一个风险点（Rust 环境缺失）连开始条件都不满足。
- libmpv 是播放质量最高的开源引擎（硬解、字幕、音轨、seek 全齐），Python 绑定成熟。
- SQLite 满足全部本地索引需求（增量扫描、标签、历史），零部署成本。
- GPL 注意事项：libmpv 为 LGPL/GPL 混合构建，WonderfulUI 代码**只参考了格式文档思路，
  未复制源码**；本项目解析层为独立实现。若未来直接复用其 GPL 代码，则整个项目需按 GPL 发行。

## 代价与重估条件

- Python 打包（PyInstaller）体积约 150-200 MB，可接受。
- 若未来需要 OBS 深度集成（libobs 进程内嵌入），C++/Rust 绑定更顺，
  届时保留 Python UI、通过 obs-websocket 控制的方案仍然成立，无需换栈。
