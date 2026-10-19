# Roadmap

## Phase 0 — 环境调查与技术可行性 ✅（2026-10-19）
- [x] 环境盘点（Python/Node/FFmpeg/OBS/GPU）
- [x] WonderfulUI 调研 + ACLOS 格式逆向验证
- [x] libmpv + Qt 播放验证（d3d11va）
- [x] ADR-001 技术栈决策

## Phase 1 — 无畏时刻库 ✅（2026-10-19）
- [x] 只读解析适配层（加密库/对局/视频/事件）
- [x] SQLite 索引 + 增量后台扫描
- [x] 六维筛选、搜索、收藏、标签、历史
- [x] 虚拟化网格 + 缩略图懒加载（ACLOS 封面优先）
- [x] 播放 + 事件时间轴 + 续播
- [x] 六套特工主题 + 设置页可视化切换（热生效、持久化）
- [ ] 端到端 GUI 手动验收（用户实际操作一遍）

## Phase 2 — 通用播放器 ✅（2026-10-19）
- [x] 拖放打开文件/文件夹
- [x] 播放列表（当前视图快照，点击切换、自动连播）
- [x] 字幕/音轨选择 UI（内嵌轨、外挂 SRT/ASS、延迟微调）
- [x] 画面比例覆盖、一键截图
- [x] 文件关联与「打开方式」（HKCU，可撤销，不抢默认）
- [x] 快捷键（Space / ←→ / Shift+←→ / ↑↓ / F / [ ] / S / N P）
- [x] 容器兼容与损坏文件容错（MP4/MOV/AVI/WEBM/MKV，中文+空格+特殊字符名）
- [ ] 播放列表循环 / 随机 / 拖拽排序（下一轮迭代）
- [ ] ASS 复杂特效与 GBK 字幕专项验证

## Phase 2.5 — 用户反馈修复 ✅（2026-10-19）
- [x] 后台串音：播放改为「一视频一标签」，关标签即销毁引擎，后台标签暂停+静音
- [x] 标签不是孤儿：侧边栏「▶ 正在播放」入口
- [x] 后台标签不再驱动全局动作（连播跳一条的根因）
- [x] 崩溃日志基础设施（faulthandler / excepthook / Qt 消息处理器）+ 设置页入口
- [x] 引擎回调加固（消除 `Signal source has been deleted` 类致命异常）
- [ ] **一次性复现验证**：用户再次遇到崩溃时提供 `logs/` 下的日志以最终定因

## Phase 2.6 — 体验与稳定性（用户截图反馈）✅（2026-10-19）
- [x] 首个视频画面居中：原生画面窗口位置校正（`_sync_native_surface`）
- [x] 已删除的源文件默认不陈列（db 层统一策略 + 设置页开关）
- [x] 标签关闭按钮不再越界（自绘 18×18 按钮），补 Ctrl+W
- [x] **修掉两个必崩缺陷**：退出时 libmpv DLL detach 崩溃、双引擎并发音频输出崩溃
- [x] 音频缓冲/缓存缓解毛刺；mpv 错误与音频告警写入 session 日志
- [ ] 「声音有锯齿感」最终确认（需用户复听 + 日志）
- [ ] 考虑单引擎架构以彻底消除多实例问题（当前方案已实测稳定，暂不重构）

## Phase 2.7 — 启动方式 ✅（2026-10-20）
- [x] `start.bat` 静默启动（`pythonw`，不留控制台），参数转交程序
- [x] `debug.bat` 保留控制台 + 退出码释义（含 0xC0000005 在 cmd 下的负数形态）
- [x] `setup.bat` 一次性环境准备：找 Python（PATH / 启动器 / 常见安装位置 / 手动输入）
      → 建 `.venv` → 装依赖（失败自动换清华镜像）→ 检查 `vendor/mpv`、`vendor/obs`
- [x] 三个脚本用临时目录 + 桩程序逐条验证（成功 / 秒退 / 缺环境 / 参数传递）
- [x] `.gitattributes` 锁 `.bat` 的 CRLF；脚本统一 GBK 编码
- [x] 打包方案文档 `docs/packaging.md`（方案已定，未执行）

## Phase 3 — 录屏工作台（进行中）
- [x] OBS 便携版就位（`vendor/obs/`，免安装），实测 `obs-websocket` 监听 4455 通过
- [ ] WebSocket 握手 / 认证 / 事件订阅（`tools/probe_obs.py` 已能拉起 OBS）
- [ ] 录制模式（屏幕/窗口/游戏窗口/显示器）与参数预设
- [ ] 编码器探测（NVENC / QSV / AMF / x264）与不支持的项禁用
- [ ] 全局热键（开始/停止/呼出控制窗）+ 冲突检测
- [ ] 托盘 + 录制状态同步 + 成品自动入库

## Phase 4 — 体验与性能
- [ ] 大库扫描基准（benchmarks.md）
- [ ] 缩略图调度去抖、播放启动耗时
- [ ] 错误恢复、统一空状态与加载态

## Phase 5 — 交付
- [x] 打包方案定稿（`docs/packaging.md`）：PyInstaller onedir、两个 exe、可选 OBS
- [ ] `PySide6` → `PySide6-Essentials`（省 400 MB+，需重跑全部 smoke）
- [ ] `ensure_mpv_dll()` 冻结分支（`sys._MEIPASS`）+ 统一 `runtime_paths.py`
- [ ] 图标 `resources/icon.ico` + VERSIONINFO
- [ ] PyInstaller 构建，产出 `ReplayStudio.exe` / `ReplayStudio-debug.exe`
- [ ] 干净机器验证清单（`docs/packaging.md` 第 8 节）
- [ ] `LICENSES/`（mpv GPLv2+ / OBS GPLv2 / Qt LGPLv3 + 源码获取说明）
- [ ] 用户手册 + 故障排查
