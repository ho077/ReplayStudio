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

## 5. 遗留

- `0xe24c4a02`（LuaJIT 展开异常）仍在日志里出现，尽管引擎已经设了
  `load_scripts=False`。它本身被 LuaJIT 自己接住、不影响运行，但说明选项可能没生效，
  值得单独查一次。
- 本轮所有结论都建立在"探针必须用真实事件循环"之上；`tools/` 下若还有用
  `processEvents()` 泵时间的探针，其生命周期类结论都需要重新测一遍。
