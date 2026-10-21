# Devlog 007 — 切主题就崩：把「关闭标签」拆干净之后才找到的两个假象

- 日期：2026-10-21 ~ 10-22
- 触发来源：用户实际使用反馈「我使用时它又崩溃了，我切颜色时切了几遍，点不动视频了」
- 涉及文件：
  - 新增 `src/replaystudio/ui/qt_teardown.py`（按 SignalInstance 逐个断开；销毁子树）
  - `src/replaystudio/ui/player_page.py`（`release()` 改用真正生效的断开 + 销毁子控件）
  - `src/replaystudio/media/player.py`（`disconnect_all()` 静音 PySide6 的无害告警）
  - 新增探针：`tools/probe_disconnect.py`、`probe_page_refs.py`、
    `probe_qss_crash.py`、`probe_crash_bisect.py`
  - 修正：`tools/probe_theme_leak.py`、`probe_page_refs.py`（必须用**真实事件循环**）

## 1. 先修好尺子：所有探针之前都在用假的事件循环

一开始 `tools/probe_theme_leak.py` 报告「关掉 3 个标签，3 个都还活着」，于是顺着
`gc.get_referrers` 一路追，追到 `_STYLED` 强引用、`lambda p=page` 捕获……这些**确实都是
真问题并且已修**，但修完之后探针仍然报 3/3。

真正的坑在这里：探针用

```python
while time.time() < end:
    app.processEvents()
    time.sleep(0.02)
```

来"转一下事件循环"。而 Qt 的 `QObject::deleteLater()` 明确要求**控制流回到事件循环**
才会执行 DeferredDelete。`processEvents()` 不投递它 —— 于是每个被测控件都"永远活着"。

换成真实的嵌套事件循环后：

```python
loop = QEventLoop()
QTimer.singleShot(int(seconds * 1000), loop.quit)
loop.exec()
```

同一个控件 `shiboken6.isValid()` 立刻变 False。**之前的"泄漏"有一半是测量误差。**

> 教训：先证明测量工具可信，再信它的结论。这一轮里我差点把 Widget 拆成碎片去迎合一个
> 不存在的泄漏。

## 2. 真正的第一个 Bug：`QObject.disconnect()` 在 PySide6 里根本不存在

`PlayerPage.release()` 里原本写着：

```python
try:
    self.disconnect()
except (RuntimeError, TypeError):
    pass
for child in self.findChildren(QObject):
    child.disconnect()
```

`tools/probe_disconnect.py` 直接把这个假设摆到阳光下问：

```
plain Holder (baseline)                        SURVIVES
      child.disconnect() -> TypeError('QObject.disconnect(): not enough arguments. …')
      self.disconnect() -> TypeError('QObject.disconnect(): not enough arguments. …')
weakref after child.disconnect()               SURVIVES
weakref after btn.clicked.disconnect()         released      <- 换这个就真断了
```

**零参数形式只存在于 `SignalInstance`（`btn.clicked`）上，不在 `QObject` 上。**
而我们的 `except TypeError` 把报错吞了，于是这段清理代码跑了半年，一次都没生效。

修法：新增 `ui/qt_teardown.py`，遍历 metaObject 找出每个信号，逐个
`SignalInstance.disconnect()`。断开数量也会写进 session 日志
（实测一次标签关闭断开 583 个信号），下次再看日志就知道清理跑没跑。

顺手压掉了 `libpyside: Failed to disconnect (None)` —— 那是"这个信号本来没接东西"的
提示，不是错误，不该刷屏。

## 3. 第二个 Bug（就是用户报的崩溃）：断子控件的信号会炸

修好 2 之后，`probe_theme_leak.py` 从"页面不释放"变成了**真的段错误**：

```
### cycles=0 themes=12   -> EXIT=0    只切主题，没事
### cycles=4 themes=0    -> EXIT=0    只开关标签，没事
### cycles=1 themes=12   -> EXIT=139  关一个标签后再切主题，必崩
### cycles=2 themes=12   -> EXIT=139
```

`faulthandler` 的落点非常干净：

```
Current thread 0x8c38:
  File "…/ui/theme.py", line 293 in apply_theme      <- app.setStyleSheet(QSS)
  File "…/ui/main_window.py", line 162 in apply_theme
```

### 3.1 先排除 mpv

崩溃栈里同时有 `0xe24c4a02`（LuaJIT 的栈展开异常，见 005），很像是 libmpv 的锅。
于是写 `probe_crash_bisect.py`：把 `MpvPlayer` 整个换成同 API 的假引擎。

```
--- dummy ---     mode: dummy engine (no libmpv at all)     EXIT=139
```

**没有 mpv 照样崩。** 嫌疑排除，mpv 只是被 LuaJIT 异常误伤的路人。

### 3.2 再排除"我们的控件类型"

`probe_qss_crash.py` 造了 7 种最小场景（普通控件 / 带 `WA_NativeWindow` 的宿主 /
带独立 stylesheet 的子控件 / 事先 unpolish / 先清空 stylesheet / 先摘出父树 …），
全部存活。所以也不是"原生窗口 + stylesheet"的组合。

### 3.3 最后是逐项关掉拆除步骤

```
off: purge,disconnect,unregister  -> survived
off: disconnect                   -> survived      <- 关掉它就活
off: purge                        -> EXIT=139
off: unregister                   -> EXIT=139
          d-root（只断页面自身）  -> survived
        d-custom（只断自定义信号）-> survived
```

结论：**元凶是"断开整棵子控件树的信号"**。把 Qt 自己建在组合控件内部的连接
（滚动区、列表视图那套）也一并断掉后，下一次 `QApplication::setStyleSheet()`
就在 Qt 内部踩到野指针。

### 3.4 修法与理由

`release()` 改成只断 **页面自身** 的信号（`qt.disconnect_object(self)`），
子控件不再逐个断开，而是整个销毁（`qt.purge_children`）。

这样做既安全又够用：连接不可能活得过它的发送者。子控件一销毁，那些
`lambda _=False, v=…: self.seek_to(v)` 的闭包随之消失，页面 ⇄ 子控件的循环自然断开。
不需要为了"断开"而碰 Qt 的内部连接。

`qt_teardown.disconnect_tree()` 保留在代码里，但标了醒目的"不要用在页面上"——
它是这个崩溃的可复现配方，`probe_crash_bisect.py` 靠它做回归。

## 4. 结果

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 开 4 个标签再全部关闭，页面残留 | 4/4 | **0/4** |
| 关一个标签后连切 12 次主题 | 段错误（必现） | **EXIT=0** |
| 开着视频连切 12 次主题（用户实际操作） | — | **EXIT=0**，跑两遍 |
| `probe_theme_crash.py` 连续 20 轮 | 中途死 | **survived all rounds** |

回归套件（全部 EXIT=0）：

| 脚本 | 结果 |
| --- | --- |
| `tests/smoke_core.py` | SMOKE OK |
| `tools/smoke_tabs.py` | 25/25 |
| `tools/smoke_playback.py` | PLAYBACK OK |
| `tools/smoke_containers.py` | 通过 |
| `tools/smoke_hidden_missing.py` | 通过 |
| `tools/smoke_gui.py` | SMOKE GUI OK |
| `tools/smoke_phase2.py` | 19/19 |
| `tools/smoke_exit.py` | 2/2，两次退出码 0 |
| `tools/probe_mpv_lifecycle.py` | 5 个周期，无残留线程 |

## 5. 第二轮：同一条日志里还藏着另外两个坑

修完 3 之后 `tools/smoke_tabs.py` 变成 **2/3 段错误**。注意这个比例是**对照出来的**——
改之前我只跑过一次就报告 25/25，是运气。凡是"偶发"先跑三遍再下结论。

### 5.1 坑 C：GUI 线程上的同步属性写入

崩溃栈里主线程一律停在 `python-mpv` 的 `_set_property`：

```
  mpv.py:2113 in _set_property
  player.py:444 in suspend_audio
  player_tabs.py:372 in _park
```

`mpv.<prop> = v` 是 `mpv_set_property`，一次**同步往返**。而 pause / mute / volume /
`aid=no` 这些全都从 GUI 线程发出。引擎正忙（加载中、或正被另一个线程销毁）时，
调用方就卡在 libmpv 里 —— 这就是"点不动视频"。

修法：新增 `MpvPlayer._write()`，一律走 `command_async("set", name, value)`，
投递即返回；布尔值转成 mpv 的 `yes`/`no`。所有 setter 改为先更新镜像再异步写入。

|  | smoke_tabs 连跑 3 次 |
| --- | --- |
| 修 3 之后 | 2/3 段错误 |
| 修 3 + 5.1 | 3/3 跑完（0 次段错误） |

### 5.2 坑 D：faulthandler 的 `all_threads=True` 是自杀开关

日志的前 90% 都是这个：

```
Windows fatal exception: code 0xe24c4a02     <- 反复出现
…
  <invalid frame>
Windows fatal exception: access violation    <- 然后就没了
```

`0xe24c4a02` = `LJ_EXCODE | LUA_ERRRUN`，是 **LuaJIT 用"抛 SEH 异常再自己接住"来展开栈**，
它自己会处理，本身无害。但 `faulthandler.enable(all_threads=True)` 装的
**vectored exception handler 排在最前面**，每次都要**遍历所有线程的栈**并打印 ——
其中那个线程正被 LuaJIT 改写栈指针，于是 dump 里出现 `<invalid frame>`，
紧接着就是访问违规。**所谓"随机崩溃"，一大半是这次 dump 自己造成的。**

改成 `all_threads=False`：只 dump 出错的那一个线程。真正要看的就是它，
代价几乎为零，收益是进程活下来。

|  | smoke_tabs 连跑 4 次 |
| --- | --- |
| 修 3 + 5.1 | 仍有段错误（3 次里 1 次） |
| 再修 5.2 | **4/4 = 25/25，EXIT=0** |

补充：`--load-scripts=no` **关不掉**这些异常（`tools/probe_luajit_count.py`
实测关了照样抛）。所以它只能靠 5.2 绕开，不能靠消除。

### 5.3 顺带修掉：画面校正自己制造偏移

`_sync_native_surface()` 用 `host.mapTo(parent, QPoint(0,0))` 算坐标就交给
`SetWindowPos`。但带 `WA_DontCreateNativeAncestors` 时，host 的**真实父 HWND 不是
`stage` 而是更上层的原生祖先**，于是坐标基准错了。
`tools/probe_surface_offset.py` 抓到现行：手动调一次这个函数，偏移从
`(0,0)` 直接变成 `(-282,-148)`。

改成：取 host 的**屏幕坐标**，用 `GetParent()` 拿到真实父 HWND，
`ScreenToClient()` 转过去再 `SetWindowPos`。

同时修了 `smoke_tabs.py` 的测量方式 —— 它调 `host.parentWidget().winId()`，
**这个调用本身会把 stage 变成原生窗口**，从而在测量过程中把 surface 重新认父，
于是报出一个屏幕上根本看不见的 283px 偏移。改用 `mapToGlobal`（不会创建窗口）比较
"HWND 的实际位置 vs Qt 认为的位置"，这才是真正要守住的不变量。

## 6. 验证汇总（第二轮之后）

| 脚本 | 结果 | 退出码 |
| --- | --- | --- |
| `tests/smoke_core.py` | SMOKE OK | 0 |
| `tools/smoke_tabs.py` | 25/25（连跑 4 次全过） | 0 |
| `tools/smoke_playback.py` | PLAYBACK OK | 0 |
| `tools/smoke_containers.py` | 9/9 | 0 |
| `tools/smoke_hidden_missing.py` | 11/11 | 0 |
| `tools/smoke_gui.py` | SMOKE GUI OK | 0 |
| `tools/smoke_phase2.py` | 19/19 | 0 |
| `tools/smoke_exit.py` | 2/2 | 0 |
| `tools/smoke_diagnostics.py` | 3/3 | 0 |
| `tools/probe_crash_bisect.py real/keep` | survived | 0 |
| `tools/probe_theme_crash.py` | survived all rounds | 0 |
| `tools/probe_mpv_lifecycle.py` | 无残留线程 | 0 |

## 7. 遗留

- **`probe_theme_leak.py 4 12` 仍有约 1/3 概率段错误**（4 次开关标签 + 12 次切主题，
  属于压力强度）。栈仍然落在 `QApplication::setStyleSheet`，前面是 `0xe24c4a02` 连发。
  与 devlog 005 记录的是同一类问题：**反复创建/销毁 mpv 引擎**会损坏进程状态
  （"不用 Qt、真实音频+真实视频输出、2 次生命周期 → 崩"）。
  试过"销毁前显式 `unpolish`"来清 Qt 样式表缓存，**无效反而更差（3/4）**，已回退。
  根治方向是 **不再反复创建引擎**（关闭标签时把页面连同引擎**回收复用**而不是销毁），
  改动面较大，列为下一步。
- `0xe24c4a02` 无法消除：`--load-scripts=no` 实测关不掉它。只能靠
  `all_threads=False` 绕开其放大效应。这意味着 `crash-*.log` 里仍会有这类条目，
  **看到它不要误判为崩溃原因**。
- 所有生命周期类结论都建立在"探针必须用真实事件循环"之上；`tools/` 下若还有用
  `processEvents()` 泵时间的探针，其结论都需要重测一遍。
- 教训一条：判定"偶发"之前先连跑三遍。本轮有两次差点把随机性当成因果。
