# Devlog 004 — 修掉两个真实缺陷：后台串音 + 进程静默退出

- 日期：2026-10-19
- 触发来源：用户实际使用反馈
  1. 「点进一个视频看了会，点击返回后，后台还是有声音传出来」
  2. 「我刚刚使用时程序崩溃，自己退出了」
- 涉及文件：
  - `src/replaystudio/ui/player_tabs.py`（新增：一视频一标签的播放宿主）
  - `src/replaystudio/core/diagnostics.py`（新增：崩溃与运行日志）
  - `src/replaystudio/media/player.py`（引擎回调加固、关闭顺序）
  - `src/replaystudio/ui/player_page.py`（`release()` 生命周期、行变更信号）
  - `src/replaystudio/ui/main_window.py`（播放路由改为经标签宿主）
  - `src/replaystudio/ui/widgets.py`（侧边栏新增「正在播放」入口）
  - `src/replaystudio/ui/settings_page.py`（打开日志目录）

## 1. 问题一：返回后后台还有声音

### 1.1 根因

`MainWindow.navigate()` 只做了 `self.stack.setCurrentWidget(page)`。播放页被**隐藏**，但：

- 页面对象还活着，`PlayerPage._timer` 继续每秒 tick（还在写播放进度到数据库）；
- `MpvPlayer` 实例继续解码，音频设备继续出声；
- d3d11va 硬件解码把新帧继续推进一个**不可见的原生子窗口**。

这不是「忘记 stop」的小疏漏，是**架构层面缺了「播放会话」这个概念**：一个被隐藏的页面
仍然持有活跃的解码器，说明"页面可见性"和"播放生命周期"被混为一谈了。

顺带说，第三条也是**进程静默退出最可能的成因**：让 libmpv 长时间向隐藏窗口渲染，
不是它设计上要长期承受的状态。

### 1.2 方案：一个标签 = 一次播放会话

放弃「单例播放页 + 换片」，改成 `PlayerTabs`（`QTabBar` + `QStackedWidget`）：

| 行为 | 语义 |
| --- | --- |
| 打开视频 | 新建标签，新建独立 `PlayerPage` + 独立 `MpvPlayer` |
| 关闭标签 / 按「返回」 | `release()` → `stop` → `terminate` 引擎 → 释放原生 surface → `deleteLater` |
| 切到其他标签 | 旧标签**暂停 + 静音**，位置保留（`_park`） |
| 离开整个播放区 | `park_all()` 静音全部标签 |
| 回到播放区 | `activate_current()` 恢复被 park 的播放状态 |
| 标签数上限 | 6（防止拖入 20 个文件起 20 个解码器耗尽显存） |

关键点：**降级为"暂停+静音"而不是关闭**。用户切到另一个标签看别的东西，回来还想接着看，
直接关掉会丢掉位置；但只暂停不静音仍然会占着音频设备（有些驱动会持续输出静音流，
更糟的是某些格式下 `pause` 生效有延迟，前几百毫秒仍会出声）。所以两者都做。

### 1.3 顺带修掉的架构问题：后台标签不能驱动全局动作

第一版直接把每个标签的信号连到主窗口，结果 `smoke_phase2.py` 的连播用例从 19/19 掉到 18/19 —
**后台标签播放到结尾也 emit 了 `nextRequested`，主窗口把它作用在当前前台标签上，于是连播跳了一个**。

修法：`_relay()` 只在 `page is self.current_page()` 时才向外转发。

```
page.nextRequested.connect(lambda p=page: self._relay(p, self.nextRequested))
```

### 1.4 让标签不是"孤儿"

改完之后 `smoke_tabs.py` 抓到 18/19：关闭标签后回到媒体库，侧边栏里没有任何入口能回到
还开着的标签 —— 用户只能关掉一个再开一个。补了侧边栏「▶ 正在播放」项（有标签时才显示）。

## 2. 问题二：程序自己退出

### 2.1 先解决"没有证据"

`%LOCALAPPDATA%\ReplayStudio\` 下只有一个数据库，没有日志、没有转储。窗口模式启动时
stderr 根本没人看，所以"崩溃"对我们来说是不可调试的。

`core/diagnostics.py` 在 `main()` **第一行**（早于 `QApplication`）装三个独立通道：

| 通道 | 捕获什么 | 为什么必须单独有它 |
| --- | --- | --- |
| `faulthandler`（`all_threads=True`） | 原生访问违例 / SIGSEGV | libmpv 死在事件线程上时，Python 层什么都看不到 |
| `sys.excepthook` | 未捕获异常 | Qt 槽里抛出的异常不会打印到任何地方 |
| `qInstallMessageHandler` | `qWarning` / `qFatal` | Qt 内部告警在窗口构建版里默认消失 |

写入 `%LOCALAPPDATA%\ReplayStudio\logs\session-*.log`，`faulthandler` 另写 `crash-*.log`；
启动时清理 7 天前的日志，防止无限增长。所有写入都是 `try/except OSError: pass` ——
日志系统自身的失败绝不能拖垮主程序。

### 2.2 已知的真实崩溃诱因：`Signal source has been deleted`

修 Bug 一的过程中，`smoke_phase2.py` 输出了这条告警。含义是：**mpv 事件线程正在向一个
已被销毁的 Qt 对象 emit 信号**。这个 RuntimeError 抛在 Qt 事件循环之外，不被任何 `try`
捕获，在 PySide6 里会直接把进程带走。

两个方向同时加固：

1. `MpvPlayer._guard()` 包住**每一个**回调（11 个 property observer + event callback）。
   先查 `self._alive`，`RuntimeError` 出现就把 `_alive` 置 False 后续全部变空操作。
2. `shutdown()` 顺序调整为：`_alive = False` → `command("stop")` → `terminate()`。
   先降旗再停引擎，让已经排队的事件立刻变成空操作；`stop` 在 `terminate` 之前
   是为了先释放音频设备和视频输出，避免引擎在渲染中途被拆。

`PlayerPage.release()` 的顺序是：**先 `self.player = None`**，再调 `shutdown()`。
先断引用，让仍在跑的 mpv 回调撞上 `if not self.player` 守卫，而不是访问一个正在被
terminate 的对象。

### 2.3 验证方式

`tools/smoke_diagnostics.py` 用**子进程**分别制造两种真实故障，然后检查日志内容：

| 用例 | 手段 | 断言 |
| --- | --- | --- |
| Python 异常 | `raise RuntimeError(...)` | 退出码 ≠ 0 且 session 日志含完整 traceback |
| 原生访问违例 | `ctypes.string_at(0)` | 退出码 ≠ 0 且 crash 日志含 faulthandler 全线程栈 |
| 日志轮转 | 伪造 1999 年的日志文件 | 启动后被清理 |

用子进程是因为这两件事**真的会把进程打死**，在测试进程内做会把测试一起带走。
`LOCALAPPDATA` 重定向到临时目录，不碰用户真实日志。

### 2.4 给用户一个取证入口

设置页「索引与缓存」新增「打开日志目录」，并直接显示本次运行的日志路径。
否则用户报"崩了"的时候，我们连让他发哪个文件都说不清。

## 3. 回归测试

| 脚本 | 覆盖 | 结果 |
| --- | --- | --- |
| `tools/smoke_tabs.py`（新增） | 21 项：独立引擎、关闭后销毁、后台静音、返回无残留音频、标签上限、全部关闭回媒体库、退出不崩溃 | 21/21 |
| `tools/smoke_diagnostics.py`（新增） | 3 项：Python 异常 / 原生访问违例 / 日志轮转 | 3/3 |
| `tools/smoke_phase2.py` | 19 项（含连播只前进一项、文件关联闭环） | 19/19 |
| `tools/smoke_containers.py` | 9 项容器兼容 + 截断文件 + 垃圾文件不崩溃 | 9/9 |
| `tools/smoke_playback.py` | 真实录像播放、seek、硬件解码 | OK |
| `tools/smoke_gui.py` / `tests/smoke_core.py` | 导航、缩略图、索引统计 | OK |

`smoke_phase2.py` / `smoke_containers.py` 同步改造：它们原先持有 `win.player_page` 这个
固定引用，改成每次操作后重新取**当前标签**的页面（`player_of(win)`）。

## 4. 遗留与未验证

- **本次崩溃的直接原因仍未百分百确认**。`Signal source has been deleted` 与「向隐藏窗口
  持续渲染」是两条高度可疑路径，都已消除，但用户遇到的那次没有日志。现在再崩就有
  `crash-*.log` 了，需要用户复现一次才能定论。
- 标签数上限 6 是经验值，未做显存压力测试。
- 侧边栏「正在播放」项的显示/隐藏依赖 `changed` 信号，极端并发关闭下未做压测。
