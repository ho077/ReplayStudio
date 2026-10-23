# Devlog 011 — 关闭慢：一个 tasklist 和一个 3 秒的等待

日期：2026-10-24
输入：「这个软件关闭时有点慢，你知道为啥吗」。

## 1. 打点，别猜

`MainWindow.closeEvent` 只做三件事：关掉所有播放标签、收 OBS、等缩略图线程池。
给这三步各自插了计时（`tools/probe_shutdown.py`），结果：

```
### 0 个标签、没用过录屏
  TOTAL closeEvent                      285.5 ms
  record.close_down (OBS?)              274.3 ms     <-- OBS 根本没在跑
  thumbs.pool.waitForDone                 0.0 ms
### 2 个标签
  TOTAL closeEvent                      359.7 ms
  close_all (all play tabs)              81.1 ms
  record.close_down (OBS?)              273.4 ms
### 2 个标签 + 20 个缩略图任务排队
  TOTAL closeEvent                      415.8 ms
  record.close_down (OBS?)              346.9 ms
  thumbs.pool.waitForDone                 2.3 ms
```

**274 ms 花在"确认 OBS 没在跑"上**：`obs_launcher.stop()` 第一件事是
`is_running()` → `pids()` → 起一个 `tasklist` 子进程（实测 ~250 ms）。而且不管
你有没有用过录屏页，退出都会走这一趟。

另外两个隐患没在这组数据里显形，但它们更凶：
- `thumbs.pool.waitForDone(3000)`：只要有 ffmpeg 还在跑就**等满 3 秒**（这次
  ffmpeg 太快所以只花了 2.3 ms）。
- 真在跑 OBS 时，`stop()` 三个阶段（WM_CLOSE 等 10 s、taskkill 等 8 s、
  taskkill /f 等 7 s）**加起来最多 25 秒**。

## 2. 修了什么

1. **没碰过 OBS 就别去收它**（`recording.py`）。Recorder 记一个
   `_obs_touched`；`disconnect_obs()` 没动过就直接返回。运行标记本来就在下次
   启动时会清，退出时去清是白跑一趟。顺带一个新行为：用户**自己**开的 OBS
   不再被我们杀掉（以前退出会连它一起关）。
2. **记住 PID，别问全系统**（`obs_launcher.py`）。`_KNOWN_PIDS` 记录我们启动
   的（或连接时发现的）OBS 进程；`pids()` 先用 `OpenProcess` +
   `GetExitCodeProcess` 查这些 PID（ctypes，0 成本），全死了才回退 tasklist
   ——保留回退是为了不漏掉用户自己启动的 OBS（否则 `start()` 会在同一个
   profile 上开第二个）。
3. **退出给 OBS 一个总预算**：`stop(..., budget=3.0)`，三个阶段共享一个截止
   时间，不再各等各的。交互式「停止 OBS」按钮走默认（25 s），不受影响。
   预算耗尽仍活着时，照样清运行标记，免得下次启动被赖上。
4. **缩略图不等了**：退出时 `pool.clear()` 丢掉排队任务，正在跑的只给 250 ms
   宽限（原来是 3000 ms）。没跑完也无害——缓存是 `.part + rename` 原子写的，
   谁都看不到半个 JPEG。

## 3. 结果

| 场景 | 前 | 后 |
|---|---|---|
| 0 个标签（从没用录屏） | 285.5 ms | **3.3 ms** |
| 2 个标签 | 359.7 ms | **81.1 ms** |
| 2 个标签 + 20 个缩略图任务 | 415.8 ms | **81.1 ms** |
| OBS 正在运行（`probe_obs_quit`） | 最多 25 s | **2.1–2.6 s**（WM_CLOSE 干净退出） |

剩下的 ~30 ms/标签是 mpv 引擎收尾（`stop` + `quit` + 事件线程 join），
属于真实成本，没动。

## 4. 验证

`tools/probe_shutdown.py` 可按标签数/缩略图数复跑；`tools/probe_obs_quit.py`
真起 OBS 再关（实测 2.64 s，OBS 确实退出、不是强杀）。回归：smoke_core、
smoke_tabs 25/25、smoke_playback、smoke_containers 9/9、smoke_hidden_missing
11/11、smoke_gui、smoke_phase2 19/19、smoke_exit 2/2、smoke_diagnostics 3/3、
smoke_stuck_fix、smoke_db_perf、**smoke_obs 11/11**（OBS 生命周期）全绿。

另：修了一个我自己埋的测试 bug —— `smoke_db_perf` 之前往**用户真实库**里写
`probe` 键来测设置读写，第二次跑就拿到旧值必失败。功能测试改到临时库，
并把那次残留的 `probe` 从真实库删掉了。
