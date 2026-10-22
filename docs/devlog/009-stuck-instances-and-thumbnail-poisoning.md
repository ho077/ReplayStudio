# Devlog 009 — 「卡住了」：三个实例、毒化的缩略图缓存与 30 秒的忙等

日期：2026-10-23 02:45 前后
输入：用户截图 —— 所有卡片停在「载入封面…」，顶部「四杀时刻 (」标签被截断，
汇报「软件卡住了」。

## 1. 现场取证

日志目录里躺着 **三个都没退出的实例**（01:53 / 02:21 / 02:33 各一个）：

```
54968 pythonw  2:33:00  CPU 0
63804 pythonw  2:31:50  CPU 11
64548 pythonw  2:33:00  CPU 50
```

用户遇到卡死后反复重开，越开越多。两个 crash 日志里只有 LuaJIT 的
`0xe24c4a02` 刷屏（已知的无害展开异常），没有真栈——**不是崩溃，是卡死**。

软件此前完全没有单实例保护。多实例的后果：

1. SQLite 写锁互斥：一个实例在扫描/写入时，另一个实例 UI 线程的同步查询
   会坐进 `sqlite3.connect(timeout=30)` 的忙等里 30 秒，界面零重绘。
2. thumbs 缓存目录并发写同一 JPEG（见 §3）。

## 2. 修复 A：单实例（core/single_instance.py）

- `QLockFile(user_data_dir()/app.lock)`：第二实例拿不到锁就不启动。
- 「打开方式」双击文件会启动新进程，因此第二实例把
  `{"files": [...]}` 原子写入 `open_req_<pid>_<rand>.json` 后静默退出；
  第一实例用 400ms 的 QTimer 扫描并消费，打开文件并把窗口提到前台。
- **QLocalServer 方案先做后弃**：进程内一切正常，跨真进程 `newConnection`
  在本机从不触发（tools/probe_si_cross.py，in-process 最小复现却通过，
  极难排查）。文件投递无聊但确定：写 `.tmp` + `os.replace` 保证原子，
  消费端先 `os.replace` 成 `.done` 抢占再读，天然防竞争。
- app.py：投递成功就退出，**不弹模态框**——弹出的窗口就是反馈，而
  「打开方式」流程不能被对话框阻塞（端到端实测第二实例 0.7s 退出）。

## 3. 修复 B：缩略图缓存毒化（media/thumbs.py）

`produce_thumbnail` 直接 `open(out, "wb")` 写目标文件。两个写者（多实例，
或同实例两个任务）各写一半后，留下的半截 JPEG **永远通过**
`size > 0` 的缓存判定 → `QImage` 解码失败 → failed → 下次重试读同一个
坏文件 → 无限失败循环。卡片就永远停在「载入封面…」。

- 写入改为 `_atomic_copy`：写 `.part<pid>` 临时文件 + `os.replace`
  （Windows 原子）。读者只会看到「无文件」或「完整文件」。
- `_ThumbTask` 在 `QImage` 解码失败时**删掉坏文件**再报 failed，让下一次
  有机会重新生成——否则毒化是永久的。
- `ThumbnailService` 增加 `_failed` 集合：失败的视频本会话不再自动重试。
  之前每次重绘 `data(DecorationRole)` → `request()`，250 张失败卡片 =
  永动的 ffmpeg 风暴，线程池和磁盘一直满负荷。`clear_ready()`（重扫描）
  时一并清空，给它们新机会。

## 4. 修复 C：30 秒忙等（core/db.py）

`sqlite3.connect(timeout=30)` 的 timeout 是忙等时长，而 UI 每次查询都开
独立连接。别的实例持有写锁时，主线程就在这里坐 30 秒。降到 3 秒：比我们
任何一次写都长，又短到让卡顿表现为报错而不是假死。

## 5. 顺手修：「四杀时刻 (」

分类下拉框 `setMinimumWidth(104)` 的定宽在标签变成「四杀时刻 (686)」后
必然截断。六个筛选框改用
`AdjustToMinimumContentsLengthWithIcon` + `setMinimumContentsLength(16)`，
随最长标签自适应。

## 6. 验证

- `tools/smoke_stuck_fix.py`（新增）10/10：锁互斥、请求投递/排空、
  坏缓存清理、原子拷贝、失败不重试、timeout=3。
- 端到端双进程：A 启动 6s 后 B 带真实文件启动 → B 0.7s 退出码 0，
  A 在一个轮询周期内消费请求（leftover 为空）。
- 回归：smoke_core、smoke_tabs 25/25、smoke_playback、smoke_containers
  9/9、smoke_hidden_missing 11/11、smoke_gui、smoke_phase2 19/19、
  smoke_exit 2/2、smoke_diagnostics 3/3、probe_facet_ui 0 mismatch、
  probe_facet_counts mine 0 mismatch、probe_theme_crash、
  probe_mpv_lifecycle —— 全部通过。

## 7. 遗留

- 覆盖「打开方式」注册表指向 run.py 的场景没验证（需要真实资源管理器
  双击）。
- devlog 007 的已知问题（probe_theme_leak 4 12 约 1/3 段错误）仍未根治，
  方向依旧是引擎复用。
