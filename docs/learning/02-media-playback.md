# 学习 02：视频播放是怎么工作的？（libmpv）

对应代码：`src/replaystudio/media/player.py`、`ui/player_page.py`。

## 1. libmpv 是什么？

mpv 是目前最强的开源播放器内核之一（VLC 级别，但架构更现代）。libmpv 是它的库形态：
你的程序把一个**窗口句柄**交给它，它负责解码 + 渲染 + 音频，你只管发命令、收事件。

## 2. 播放一个视频要哪几步？

看 `MpvPlayer.__init__`：

1. **把 DLL 放进 PATH**（`ensure_mpv_dll`）——python-mpv 靠 `%PATH%` 找 `libmpv-2.dll`。
2. **创建实例**并传选项：`hwdec=auto`（硬解）、`wid=<HWND>`（画进 Qt 控件）、
   `osc=no`（关掉 mpv 自带控制条，用我们自己的）。
3. **observe_property** 订阅 `time-pos`/`duration`/`pause`… —— mpv 属性变化时回调。
4. **play(path)** → loadfile。注意续播用 `start` 选项而不是加载后 seek（有竞态，见 devlog 001）。

## 3. 解封装和解码是什么？

```text
MP4/MKV 文件 ─解封装(demux)─► H.264 压缩帧序列 ─解码(decode)─► 原始图像 ─► 渲染
                                ▲ 硬解: d3d11va 让 GPU 做，CPU 几乎空闲
                                ▲ 软解: CPU 做，兼容性最好
```

验证硬解是否生效：播放时按 ` 键不存在，看我们的代码——`hwdec_current()` 属性，
设置页显示"硬件解码：自动"。实测本机播放 1080p60 时 `hwdec-current = d3d11va`。

## 4. 线程模型（最重要的一节）

mpv 内部有自己的线程。**它的回调发生在 mpv 线程里，绝不能碰 Qt 控件**。
看 `_on_time_pos`：只做一件事——`positionChanged.emit(value)`。
Qt 信号跨线程是队列投递，槽函数自动在主线程执行。这是整个封装的纪律。

**动手任务（入门）**：把 `_cycle_speed` 的 steps 改成 `[0.25, 0.5, 1.0, 4.0]`，体验效果。
**进阶任务**：解释 `player_page.py` 里 `_pending` 的存在原因（提示：页面还没 show 时
winId 还没有效）。
**挑战任务**：用 `add_subtitle()` 实现拖放 .srt 文件到播放器即加载字幕。
