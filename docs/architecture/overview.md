# 架构总览

```text
┌────────────────────────────── ReplayStudio (PySide6) ─────────────────────────────┐
│  UI 层  src/replaystudio/ui/                                                      │
│  main_window ── 导航/热键/页面编排                                                  │
│  ├── moments_page   无畏时刻：六维筛选 + 搜索 + 虚拟化网格                           │
│  ├── local_page     本地视频：文件夹浏览 + 播放列表                                 │
│  ├── player_page    播放器：mpv 画面 + 事件时间轴 + 传输控制（所有模块共用）          │
│  ├── settings_page  设置：数据源 / 硬解 / 索引维护                                  │
│  ├── grid.py        VideoGridView（Qt model/view 虚拟化，1471 项与 24 项同开销）    │
│  └── widgets/theme  深色 Fluent 主题、标题栏、侧边栏、卡片委托                       │
├───────────────────────────────────────────────────────────────────────────────────┤
│  服务层                                                                            │
│  media/player.py   MpvPlayer：libmpv 封装，全部状态以 Qt Signal 发出（线程安全）      │
│  media/thumbs.py   缩略图管线：ACLOS 封面 → FFmpeg 抽帧 → 磁盘缓存，QThreadPool 懒加载 │
│  core/scanner.py   ACLOS 增量扫描器（后台线程，按 mtime+size 判变化）                │
│  core/local_scanner.py  本地视频扫描（复用 videos 表，source='local'）              │
├───────────────────────────────────────────────────────────────────────────────────┤
│  数据层                                                                            │
│  aclos/crypto.py   hex → AES-256-CBC（key = SHA256(openid)），只读                  │
│  aclos/reader.py   WonderfulDb → Match/Video/Event 模型（快照昵称、MVP、KDA）        │
│  aclos/taxonomy.py 地图/特工/模式中文名映射（英文→简中，映射表来自游戏官方本地化）      │
│  core/db.py        SQLite（WAL）：accounts/matches/videos/events/tags/history/…     │
└───────────────────────────────────────────────────────────────────────────────────┘
```

## 数据流（以"打开无畏时刻页面"为例）

1. `main_window` 启动时创建 `IndexDatabase`（`%LOCALAPPDATA%/ReplayStudio/index.db`）。
2. `MomentsPage.refresh()` → `db.query_videos(filter)` → `grid.set_rows(rows)`。
3. 每张卡片绘制时向 model 要 `DecorationRole` → 没有 pixmap 就向 `ThumbnailService`
   请求 → 后台线程生成 → `done` 信号 → 单卡片 `dataChanged` 重绘。
4. 双击卡片 → `main_window.open_player(row)` → `PlayerPage.load(row)` →
   `MpvPlayer.play(path)`（`start` 选项承载续播位置）。

## 设计原则

- **只读 ACLOS**：解析层从不写 `AppData/Roaming/ACLOS`，索引库独立存放，可随时重建。
- **线程纪律**：mpv 事件线程与缩略图线程只 emit Qt 信号，所有 UI 修改在主线程。
- **猜测不落库**：无法从元数据确认的标签（如中文名映射失败）显示原始 ID，绝不编造。
