# 学习文档 04 — 播放器是怎样被"控制"的

> 面向：会 Python、写过深度学习脚本，但没有做过桌面播放器的人。
> 读完你应该能回答：界面上一个"字幕"按钮按下后，到字幕真的出现在画面上，中间发生了什么。

相关代码：

| 文件 | 职责 |
| --- | --- |
| `src/replaystudio/media/player.py` | 封装 libmpv。**唯一**直接跟 mpv 打交道的地方 |
| `src/replaystudio/ui/player_page.py` | 播放页。按钮、时间轴、菜单、播放列表 |
| `src/replaystudio/ui/main_window.py` | 调度：哪个片段、播完去哪、快捷键 |
| `src/replaystudio/core/assoc.py` | 「打开方式」注册 |

---

## 1. 一句话概括架构

```
用户点按钮
   ↓
PlayerPage（Qt 控件）        ← 只处理"界面"
   ↓ 调用方法 / 发信号
MpvPlayer（QObject 包装）    ← 只处理"命令与状态"
   ↓ mpv.command(...) / 属性读写
libmpv（C 库，另一个线程）    ← 真正解码、渲染、出声
```

**关键纪律**：`PlayerPage` 里没有一行 `mpv.command`，`MpvPlayer` 里没有一行 Qt 布局代码。
这条线一旦破了，就会出现"改个按钮颜色要动解码逻辑"的局面。

---

## 2. 播放一部视频，mpv 内部发生了什么

以 `player.play("D:/a.mp4")` 为例：

1. **解封装（demux）**：mpv 读 mp4 的 box 结构，拆出视频轨、音频轨、字幕轨，
   得到一条条带时间戳（PTS）的压缩包。
2. **解码（decode）**：H.264/HEVC 的压缩帧被解码成 YUV 图像。
   这一步可以交给 GPU（本项目用 `hwdec=auto` → 实测走 `d3d11va`）。
3. **同步与渲染**：视频帧按 PTS 送到渲染器，音频送到 WASAPI，两者以音频时钟为主时钟对齐。
4. **输出**：因为我们传了 `wid`（Qt 控件的原生窗口句柄），mpv 直接把画面画进那个窗口，
   **不经过 Qt 的绘制管线**——这也是截图工具抓不到画面的原因。

对应到代码，你只需要记住三件事：

```python
self._mpv.play(path)         # 解封装 + 解码 + 渲染，全在这一句里
self._mpv.pause = False      # 播放/暂停
self._mpv.command("seek", s, "absolute", "exact")   # 跳转
```

### 硬件解码到底省了什么

- 软解：CPU 把每帧 YUV 数据算出来，1080p60 大约要占 2~4 个核心。
- 硬解：解码器在 GPU 的固定单元里跑，CPU 只搬运。代价是**色彩/后处理路径变少**，
  某些非常规编码可能不支持。

本项目 `settings.hwdec` 可切 `auto / no`，实际生效值从 mpv 的 `hwdec-current` 属性读回来显示在设置页——
**显示的是真实值，不是你以为设置的值**，这一点很重要（见第 5 节）。

---

## 3. 一个按钮的完整链路：以「音轨切换」为例

### 第 1 步：拿到真实的轨道列表

```python
# media/player.py
def audio_tracks(self):
    return [t for t in self.tracks() if t.get("type") == "audio"]
```

`self.tracks()` 读的是 mpv 的 `track-list` 属性，返回的是**文件里真实存在的轨道**。
一个普通 MP4 通常只有 1 条音轨，那么菜单里就只有 1 项——不会出现"看起来能选其实没有"的假控件。

### 第 2 步：构建菜单（不弹）

```python
# ui/player_page.py
def _build_audio_menu(self) -> QMenu | None:
    if not self.player:
        return None
    menu = self._new_menu()
    cur = self.player.current_audio()
    for t in self.player.audio_tracks():
        a = menu.addAction(f"#{t['id']}  {t.get('title') or ''}")
        a.setCheckable(True)
        a.setChecked(t["id"] == cur)          # 当前轨道打勾
        a.triggered.connect(lambda _=False, i=t["id"]: self.player.select_audio(i))
    return menu
```

注意 `lambda _=False, i=t["id"]:`——**用默认参数把 `t["id"]` 绑定在当前循环轮次**。
如果写成 `lambda: self.player.select_audio(t["id"])`，所有菜单项都会指向循环结束后的最后一个轨道。
这是 Python 闭包最常见的坑之一。

### 第 3 步：真正下命令

```python
def select_audio(self, track_id: int):
    self._mpv.command("set", "aid", track_id)
```

mpv 的 `aid` 属性（audio id）是"当前选中音轨"，改它立刻生效，不需要重新加载文件。

### 第 4 步：界面回读状态

`track-list` 属性被 `observe_property` 监听，任何变化都会触发 `tracksChanged` 信号，
`PlayerPage._on_tracks()` 据此把没有字幕的"字幕"按钮置灰：

```python
def _on_tracks(self):
    subs = self.player.subtitle_tracks()
    self.btn_sub.setEnabled(bool(subs) or self._external_sub_loaded)
```

**原则：界面状态来源于引擎的真实状态，而不是"点了按钮就假设成功"。**

---

## 4. 字幕的三种来源

| 来源 | 怎么来 | 代码位置 |
| --- | --- | --- |
| 内嵌字幕 | 封装在 MKV/MP4 里的字幕轨 | `subtitle_tracks()` 直接可见 |
| 同名外挂文件 | `a.mkv` 同目录的 `a.srt`，mpv 自动加载（`sub_auto=all`） | 无需代码 |
| 手动加载 | 菜单「加载外部字幕文件…」 | `add_subtitle()` → `sub-add` |

```python
def add_subtitle(self, path):
    self._mpv.command("sub-add", path, "select")
```

字幕延迟（音画不同步时手动校准）：

```python
def set_sub_delay(self, seconds):
    self._mpv.command("set", "sub-delay", float(seconds))
```

这个值会写进设置（`settings["sub_delay"]`），下次打开别的文件继续沿用——
因为它是**用户偏好**，不属于某个文件。

---

## 5. 为什么"显示设置值"和"显示实际值"要分开

设置页有「硬件解码：自动」，但自动到底选中了什么？答案只能从引擎问：

```python
self._mpv.observe_property("hwdec-current", self._on_hwdec)   # 实际生效的解码器
```

于是设置页会显示 `libmpv 已加载 ... 当前解码 d3d11va`。
如果只显示用户选的值，用户就永远不知道自动模式到底有没有生效。

同样的思路用于码率、分辨率等任何"引擎有最终决定权"的参数——**Phase 3 录屏会大量复用这条原则**。

---

## 6. 播放列表是怎么"连"起来的

三个角色：

1. 网格页有 `rows()` —— 当前筛选结果的完整列表。
2. `main_window.open_player(row, context)` 把它存为 `self._play_context`。
3. 播完 → `player.ended` → `PlayerPage._on_ended()` → `nextRequested` → `main_window.step(+1)`。

```python
# ui/main_window.py
def step(self, delta: int) -> bool:
    cur = self.player_page._current
    idx = next(i for i, r in enumerate(self._play_context)
               if r["video_id"] == cur["video_id"])
    nxt = idx + delta
    if 0 <= nxt < len(self._play_context):
        self.player_page.load(self._play_context[nxt], 0.0)
        return True
    return False
```

`step` 返回 `False` 表示到头了——这样"播完最后一个就停下"，而不是从头循环（循环是未来的可选项）。

### 断点续播

`_record_position` 定期把 `(video_id, pos, duration)` 写进 `history` 表；
下次 `open_player` 时 `db.last_position(video_id)` 取回，通过 mpv 的 `start` 选项在加载时生效：

```python
self._mpv.start = f"{start_pos}"      # 对"下一次加载"生效，无竞态
self._mpv.play(path)
```

不要写成 `play()` 再 `seek()`——文件还没 load 完，seek 会返回 `-12`（MPV_ERROR_COMMAND）。

---

## 7. 拖放与「打开方式」

### 拖放

```python
def dragEnterEvent(self, e):
    if e.mimeData().hasUrls():
        e.acceptProposedAction()
```

窗口和播放页都接受 URL 拖放；文件夹会被递归展开成一组视频，交给 `open_paths()` 形成播放上下文。

### 「打开方式」

`core/assoc.py` 只写当前用户注册表：

```
HKCU\Software\Classes\.mkv = ReplayStudio.Video
HKCU\Software\Classes\ReplayStudio.Video\shell\open\command = "...python.exe" "...run.py" "%1"
```

写 `%1` 占位符，Windows 会把双击的文件路径替换进去；`app.py` 从 `sys.argv` 读到它就直接播。
**不改系统默认播放器**，只是让 ReplayStudio 出现在右键「打开方式」里。

---

## 8. 三个动手任务

### 入门（改一行就能看到效果）

`media/player.py::cycle_subtitle()` 里字幕循环的方向由 `sub-add` 的顺序决定。
把它改成**跳过错位字幕**：如果当前字幕语言是 `zh`，下一次切到 `en` 而不是下一条。

提示：`current_subtitle()` 返回 dict，里面有 `lang` 字段。

### 进阶（理解数据流）

给播放页加一个「A-B 循环」按钮：

1. 第一次点击记录 `A = position`，第二次记录 `B = position`；
2. 之后每次 `_tick()`（约每 250ms 跑一次）检查 `position > B` 就 `seek(A)`；
3. 思考：为什么要用 `_tick` 而不是监听 `positionChanged`？
   （提示：`positionChanged` 频率高，做 seek 会打断解码队列。）

### 挑战（真实性能问题）

现在每次切换筛选条件，网格都会重新 `set_rows()`。如果你在 1471 条里快速来回切筛选，
会看到缩略图请求被反复取消又重发。

任务：给 `ThumbnailService.request()` 加一个**按帧延迟合并**的调度（类似 debounce 0.15s），
并测量"连续切 10 次筛选"时实际发起的解码次数，前后对比写进 `docs/performance/benchmarks.md`。
