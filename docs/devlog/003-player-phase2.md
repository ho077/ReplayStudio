# Devlog 003 — Phase 2：通用播放器

- 日期：2026-10-19
- 对应提交：`feat: Phase 2 player — playlist, tracks, aspect, screenshots, file association`
- 涉及文件：
  - `src/replaystudio/media/player.py`（引擎扩展）
  - `src/replaystudio/ui/player_page.py`（播放页重写）
  - `src/replaystudio/ui/main_window.py`（播放上下文 / 拖放 / 快捷键）
  - `src/replaystudio/core/assoc.py`（新增：HKCU 文件关联）
  - `src/replaystudio/core/config.py`（新增播放行为设置项）
  - `src/replaystudio/ui/settings_page.py`（播放行为 + 文件关联面板）

## 1. 本次要解决什么问题

Phase 1 的播放器只能「从媒体库点开一个片段」，本质是**无畏时刻专用的回放器**。要成为通用播放器，
必须补齐本地文件播放的完整链路：

1. 不建库也能播：拖放文件/文件夹到窗口、命令行/「打开方式」传入路径。
2. 播放列表：当前视图就是一个隐式播放列表，需要显式化、可点击、可自动连播。
3. 字幕与音轨：SRT/ASS 外挂、内嵌字幕选择、延迟微调、音轨切换。
4. 画面与导出：比例覆盖、截图。
5. 文件关联：从资源管理器直接打开，且**可一键撤销**。

## 2. 为什么这样设计

### 2.1 只有一个播放引擎实例

`PlayerPage` 里 `self.player` 在整个生命周期只创建一次（`showEvent` → `QTimer.singleShot(0, _init_player)`）。
换片只是 `player.play(path)`，不重建 `MpvPlayer`。

原因：libmpv 实例初始化要几十毫秒到上百毫秒，且要绑定原生窗口句柄（`wid`）。每次换片重建不仅慢，
还会让窗口重绘闪烁、硬件解码器反复初始化。播放列表连播 20 个片段时差别非常明显。

### 2.2 播放上下文（context）而不是全局播放列表

`main_window.open_player(row, context=None)` 会把**当前视图的行**快照成 `self._play_context`。
网格页传给它的就是自己 `rows()` 的结果。这样：

- 「无畏时刻」里按"地图=霓虹町 + 特工=捷风"筛出的 9 条，连播时只在这 9 条里走。
- 本地视频页按文件夹浏览时，连播只在该文件夹内。
- 不需要维护「全局播放列表 + 当前页筛选」两套状态，也就不会出现"播到别的筛选结果里去了"。

### 2.3 文件关联只写 HKCU，且默认可撤销

`core/assoc.py` 只写 `HKEY_CURRENT_USER\Software\Classes`，不碰 `HKLM`、不抢系统默认播放器：

```
HKCU\Software\Classes\.mkv            →  "ReplayStudio.Video"
HKCU\Software\Classes\ReplayStudio.Video\shell\open\command
```

效果是在资源管理器的**「打开方式」列表**里多一个 ReplayStudio 条目，用户仍然可以自己选默认程序。
`unregister()` 会删除这两处键。这属于对用户系统的写操作，所以做成了设置页里的显式按钮，不做静默注册。

## 3. 核心实现

### 3.1 引擎侧：把 mpv 的属性整理成"可查询的接口"

`media/player.py` 新增了一批只读属性与命令封装，重点是**不把 mpv 的原始字典泄漏到 UI 层**：

```python
def audio_tracks(self) -> list[dict]:
    return [t for t in self.tracks() if t.get("type") == "audio"]

def select_subtitle(self, track_id: int | None):
    self._mpv.command("set", "sid", track_id if track_id else "no")
```

UI 只用 `subtitle_tracks() / current_subtitle() / select_subtitle(id)` 这三件事，
不关心 mpv 里 `sid`、`track-list`、`slang` 这些细节。

### 3.2 EOF 检测：为什么不用 `end-file` 事件

这是本阶段最值得记录的一处坑。mpv 在 `keep_open=always` 下播完会**停在最后一帧**，
很多情况下根本不会发出 `end-file` 事件，于是自动连播永远不触发。

正确的做法是监听 `eof-reached` 属性：

```python
self._mpv.observe_property("eof-reached", self._on_eof)

def _on_eof(self, _name, value):
    if value:
        self._emit_ended()
```

但 `end-file`（reason=eof）在某些路径下**也会发**，两条通道同时命中就会连播跳过一个视频。
所以加了一个"每片只报一次结束"的闩锁：

```python
def _emit_ended(self):
    if self._ended_fired:
        return
    self._ended_fired = True
    self.ended.emit()
```

`_ended_fired` 在 `play()` 和 `file-loaded` 事件里复位。

### 3.3 另一个真实 Bug：连播后下一部视频冻结在第一帧

加入闩锁后测试暴露出更严重的问题：自动连播**确实切到了下一部**，但它停在 0 帧不动。

原因：`keep_open=always` 让 mpv 在片尾把 `pause` 置为 `true`，而这个状态会被**下一个 loadfile 继承**。
用户看到的就是"播放器自己跳到下一集然后卡住了"。

修复（`player.py::play`）：

```python
self._mpv.play(path)
# keep_open 会把 pause=yes 带到下一个文件，必须显式清掉
self._mpv.pause = False
```

这条如果没抓到，Phase 3 录屏后"录完自动播下一段"也会踩同一个坑。

### 3.4 菜单拆成 build / show 两段

播放页的三个菜单（字幕、音轨、比例）原本是"构建 + `exec()`"写在一起的。
`exec()` 是阻塞的，导致自动化测试和截图都拿不到菜单对象。现在拆开：

```python
def _build_sub_menu(self) -> QMenu | None: ...   # 纯构建，可测
def _menu_sub(self):                             # 展示
    menu = self._build_sub_menu()
    if menu:
        self._over_button(self.btn_sub, menu)
```

截图脚本因此可以 `menu.ensurePolished(); menu.adjustSize(); menu.grab()` 单独渲染菜单，
再合成到窗口截图上（`docs/screenshots/phase2/02-player-submenu.png`）。

## 4. 遇到的问题与定位过程

| 现象 | 根因 | 定位方式 |
| --- | --- | --- |
| 播放失败没有任何提示 | python-mpv 事件对象字段是 `event_id`，代码读的是 `event.id`，整个分支从未命中 | `grep END_FILE` 看事件结构 + 打印 `dir(event)` |
| 自动连播不触发 | `keep_open=always` 不发 `end-file` | 监听所有事件后打印，发现只有 `file-loaded` |
| 连播跳过一部 | `end-file` 与 `eof-reached` 双通道都会 emit `ended` | 打印事件顺序，看到 `nextRequested` 先于 `ended` |
| 连播卡在第一帧 | mpv 的 `pause=true` 被下一个 loadfile 继承 | 打印 `pos=` 一直停在 5.0 不动 |
| 测试偶发失败 | 切片后 `duration` 仍是上一部的值，seek 落点错误 | 在测试里打印 `duration/position/latch` 三件套 |

补充两个 Qt/PySide 侧的坑（已写入 `troubleshooting/known-issues.md`）：

- **`QSS` 的 8 位 hex 是 `#AARRGGBB`（alpha 在前）**，写 Web 风格的 `#RRGGBBAA` 会被读成
  一个完全不透明且色相错误的颜色（选中项显示成橄榄绿就是这个原因）。
- **给信号 `connect` 一个裸 lambda，可能被 GC 回收**，槽静默失效。测试里必须持有引用。

## 5. 验证

全部为可重复执行的脚本（`tools/`）：

| 脚本 | 覆盖 | 结果 |
| --- | --- | --- |
| `smoke_phase2.py` | 打开外部文件、时长、多音轨、字幕轨、音轨切换、字幕开关、外挂 SRT、字幕延迟、比例、倍速、步长跳转、截图、播放列表、结束事件、自动连播、不跳过、文件关联闭环 | **18/18** |
| `smoke_containers.py` | MP4/MOV/AVI/WEBM/MKV(中文+空格+特殊字符名)、截断文件、垃圾文件、拖放 | **9/9** |
| `smoke_gui.py` | 导航、六维筛选、搜索、缩略图 | 通过 |
| `tests/smoke_core.py` | ACLOS 解析 → 索引 → 查询（1471 视频 / 7944 事件） | 通过 |
| `smoke_playback.py` | 真实无畏时刻录像播放、硬解、seek 回跳 | 通过 |

测试样本用 FFmpeg 现场合成（`tools/make_phase2_sample.py`）：MKV 双音轨 + 内嵌中英字幕 + 同目录 SRT 外挂，
外加 5 种容器各一份。**不触碰用户任何原始录像。**

## 6. 未验证 / 下一步

- ASS 复杂特效（定位、卡拉 OK）未做逐项比对，只验证了可加载可显示。
- 字幕编码非 UTF-8（GBK）的文件未专门测试，mpv 通常能自动探测，但未验证。
- 「打开方式」注册后从资源管理器双击的系统级行为需人工确认一次（HKCU 写入已用注册表读取验证）。
- 多显示器全屏、HDR 片源 → 进入 Phase 4。

## 7. 可以怎样继续优化

1. 播放列表补充「循环/随机」与拖拽排序（当前是上下文快照 + 顺序连播）。
2. 字幕搜索在线下载（需要联网，与"本地优先"原则冲突，需明确开关）。
3. 缩略图 hover 预览（mpv 已支持 `--preview`，但会额外占用一个解码实例）。
