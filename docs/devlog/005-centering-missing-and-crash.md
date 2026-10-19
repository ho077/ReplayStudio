# Devlog 005 — 画面偏心 / 隐藏已删片段 / 关闭按钮越界，以及由此挖出的必崩 Bug

- 日期：2026-10-19
- 触发来源：用户实际使用截图反馈
  1. 「我第一个打开的视频画面没有在最中央，而且声音有锯齿感。后面打开的视频正常了，但第三个视频也有锯齿感」
  2. 「这种源文件缺失的是我主动删的，就不要再陈列了」
  3. 「这个叉的图标都越界了，做好看点」
- 涉及文件：
  - `src/replaystudio/ui/player_page.py`（原生画面窗口位置校正）
  - `src/replaystudio/core/db.py`（缺失片段默认隐藏 + 统计口径）
  - `src/replaystudio/core/config.py`（`show_missing_files`）
  - `src/replaystudio/ui/settings_page.py`（开关 + 日志路径）
  - `src/replaystudio/ui/player_tabs.py`（自定义关闭按钮、禁止拖动排序、park 释放音频）
  - `src/replaystudio/media/player.py`（音频缓冲/缓存、`suspend_audio`、mpv 日志入库）
  - `src/replaystudio/core/diagnostics.py`（`hard_exit`）
  - `src/replaystudio/app.py`（退出路径）
  - 新增探针/测试：`tools/probe_video_rect.py`、`probe_video_center.py`、`probe_audio.py`、
    `smoke_hidden_missing.py`、`smoke_exit.py`

## 1. 第一个视频画面偏心 —— 靠测量定位，不靠猜

症状是"只有第一个偏，切一下标签又正常"，这种**只发生在首次**的现象，通常意味着某个东西在
布局定稿之前被创建了。

### 1.1 先让 mpv 自己招供

`tools/probe_video_rect.py` 读取 mpv 的 `osd-dimensions` 并与 Qt 控件几何对比：

```
qt host   : size=912x675
mpv osd   : w=1140 h=844
```

1140/912 = 1.25，正好是本机 125% 缩放 —— **两边其实一致，说明尺寸没问题**。

### 1.2 再问 Windows

用 `EnumChildWindows` + `GetWindowRect` 拿到真实窗口矩形：

```
host GetParent 726652
  level0: hwnd=988712 rect=(946,418,1140,844)   <- video_host
  level1: hwnd=726652  rect=(663,273,1140,844)  <- stage
```

**同一个位置、同一个尺寸，但 video_host 的窗口原点比父窗口偏了 (283,145) 像素。**
再对比 Qt 侧的 `mapToGlobal`：`stage` 报 (530,218)、`video_host` 报 (757,334) ——
`video_host` 自己的 `geometry()` 明明是 `(0,0,912,675)`，完全贴合 stage。

结论：**Qt 认为子控件在 (0,0)，而它的原生 HWND 实际在 (283,145)**。
带 `WA_NativeWindow` 的普通 QWidget 会拿到真 HWND，而这个 HWND 的位置是在创建那一刻
按当时的几何定的；第一个播放页是在主窗口还没被窗口管理器摆好位置时创建的，位置就定歪了。
之后布局只改了尺寸、没把位置纠正回来。切成两个标签再切回来，触发一次完整的重新布局，
位置才被修正 —— 这正好解释了"第二个开始就正常"。

### 1.3 修法

`PlayerPage._sync_native_surface()`：用 `SetWindowPos` 把 mpv 宿主的 HWND 显式钉到
Qt 认为的位置和尺寸上。在三个时机调用：`showEvent`（延后一帧）、`resizeEvent`、
引擎初始化完成后。整个调用包在 `try/except` 里 —— 这是纯观感修复，不允许影响播放。

验证（`tools/probe_video_center.py` 抓屏 + 非黑像素包围盒）：

| 修复前 | 修复后 |
| --- | --- |
| 首标签画面 1140x844 全屏偏移、与第二个标签位置不一致 | 上下留白 102 / 102 px，水平偏差 0 |

## 2. 源文件缺失的片段不再陈列

### 2.1 策略放在数据库层，而不是每个界面各写一遍

`Database` 增加 `show_missing` 开关（默认 False）与 `missing_clause`，落到三处：

| 位置 | 行为 |
| --- | --- |
| `query_videos` | 未显式指定 `state` 时追加 `file_state != 2` |
| `recent` | 最近播放同样过滤 |
| `facets` | 侧栏地图/特工/类型计数同步过滤，否则会出现"霓虹町 12"但网格只有 9 条 |
| `stats` | `videos`/`bytes` 改为**可见**统计；新增 `all_videos`/`all_bytes` 保留原始总量 |

之所以保留在索引里而不是删除记录：文件回来自动恢复显示（测试用例覆盖），
而 `file_state = 2` 本来就会在下次扫描/「重新检查文件状态」时被重新判定。

### 2.2 保留可追溯性

用户可能是误删了想找回来，所以：

- 显式筛选「仅缺失」仍然可用（优先级高于默认隐藏）；
- 设置页保留"另有 435 个源文件已缺失，默认不显示"的说明；
- 新增开关「显示『源文件缺失』的片段」，打开后完全恢复旧行为。

### 2.3 验证

`tools/smoke_hidden_missing.py`（临时库，不碰真实索引）11 项：默认隐藏、总数、
最近播放、facets、stats 双口径、显式"仅缺失"、开关恢复、记录仍在索引、
**文件恢复后自动回到列表**。

## 3. 关闭按钮越界

原先是 `setTabsClosable(True)` + `QTabBar::close-button` 的 QSS。问题在于这个子控件由
**样式绘制**，几何尺寸不受 QSS 的 `width/height` 可靠控制，而标签又带圆角边框 ——
红色悬停背景就溢出了右上角。

改成完全自绘：`setTabsClosable(False)`，每个标签用 `setTabButton()` 放一个固定 18×18 的
`QToolButton`（objectName `tabClose`）。这样几何由 Qt 在标签矩形内摆放，**结构上不可能越界**。
QSS 只管颜色：常态 `$TEXT_FAINT`，悬停 `$CLOSE_HOVER` + 白字，按下 `$ACCENT`。

一个顺手修掉的隐患：原来的按钮/关闭逻辑按**索引**寻址，而 `bar.setMovable(True)` 允许
拖动标签 —— 拖动后 `bar` 的索引顺序和 `QStackedWidget` body 的顺序会不一致，会显示错的
播放器页面。现在按钮绑定的是 **page 对象**而不是索引，并且关闭了拖动排序。
另外补了 `Ctrl+W` 关闭当前标签。

测试：`smoke_tabs.py` 里逐个校验 `tabRect(i).contains(button.geometry())`。

## 4. 意外发现：一个几乎必现的崩溃（用户报的"自己退出了"）

写 `tools/smoke_exit.py` 验证"关闭应用"时，发现**退出码 139（段错误）**。
这个 5 分钟前还被认为是"测试脚本噪声"的东西，继续查下去发现是真问题。

### 4.1 症状与定位过程

| 实验 | 结果 |
| --- | --- |
| 真实 `app.main()`：开 3 个标签、关 2 个、关窗口 | **5/5 段错误** |
| 同上但设 `audio="no"` | 不崩 |
| 同上但设 `hwdec="no"`（音频开） | 仍崩 → 与硬解无关 |
| 不用 Qt，只反复建/销毁引擎（`vo=null`） | 不崩 |
| 不用 Qt，`vo=null` + 真实音频 | 不崩 |
| 不用 Qt，真实音频 + 真实视频输出，2 次生命周期 | 崩 |

故障码是 `0xE24C4A02` 反复出现，最后才是 `access violation`。
`faulthandler` 的日志显示崩在 `mpv.py::_loop`、`_get_property` 等位置，即 libmpv 内部。

### 4.2 两个独立的坑

**坑 A：退出时崩（已修）**
`os._exit()` 并**不能**绕过 —— `_exit()` 仍然走 `ExitProcess`，而后者会遍历所有已加载
模块的 `DllMain(PROCESS_DETACH)`，libmpv 正是死在那里。改用 `TerminateProcess`
（真正跳过 detach 遍历）。注意 ctypes 必须显式声明
`argtypes=[c_void_p, c_uint]`、`restype=c_int`，否则调用会静默失败然后落到 `os._exit`，
看起来"没生效"（我第一次就是这样被误导了一轮）。

**坑 B：运行中途崩（已修）**
修好 A 之后仍有 3/10 的概率在**开第二个标签时**直接死进程。矩阵测试把范围压到：
两个引擎**同时**各自持有真实音频输出 + 真实视频输出。

修法：后台标签被 park 时，不只是暂停+静音，还**彻底释放音频设备**
（`aid=no`，记下原轨道 id，回来时恢复）。这样任何时刻只有一个引擎持有音频设备。

| 阶段 | 10 次连续运行 |
| --- | --- |
| 修复前 | 3 次未走完流程即死 |
| 修 A 后 | 退出阶段非崩溃率下降，但中途仍 3/10 死 |
| 修 A+B 后 | **12/12 全过程干净退出** |

### 4.3 顺带：mpv 的错误和音频告警开始入库

`MpvPlayer._log` 以前只往 stderr 写 error/fatal —— 窗口模式下等于扔掉。现在
error/fatal 与**音频相关 warn（含 `underrun`）**都写进 session 日志（同一消息前 3 次
逐条记，之后每 50 次记一次，避免刷爆）。下次再报"声音有问题"，日志里就有证据。

## 5. 关于「声音有锯齿感」

诚实说明：**这一条我没有直接复现**（本机听不到、也无法采样比对）。

已做的两件事，都基于可测的证据：

1. **音频缓冲从默认 0.2s 提到 0.4s，并给本地文件开了 demuxer 缓存**
   （32 MiB + 8 MiB back，6 个标签上限下内存可控）。ACLOS 片段约 35 Mbit/s，
   而首次播放经常和缩略图抽帧同时进行，磁盘/CPU 抖动造成的欠载是最常见的
   "毛刺/锯齿"来源。`tools/probe_audio.py` 实测：`current_ao=wasapi`、
   `audio_buffer=0.4`、缓存生效、8 秒内 0 次 underrun。
2. **消除了多引擎同时持有音频设备**（见 4.2 坑 B）。这既是崩溃的原因，
   也是部分声卡在多个 shared-mode 客户端反复开关时出现杂音的经典诱因。

如果还有，请把 `%LOCALAPPDATA%\ReplayStudio\logs\` 下最新的 `session-*.log` 发我 ——
里面现在会带 `underrun` 之类的音频告警。

## 6. 验证汇总

| 脚本 | 结果 | 退出码 |
| --- | --- | --- |
| `tests/smoke_core.py` | OK | 0 |
| `tools/smoke_gui.py` | OK | 0 |
| `tools/smoke_hidden_missing.py`（新） | 11/11 | 0 |
| `tools/smoke_diagnostics.py` | 3/3 | 0 |
| `tools/smoke_tabs.py` | 25/25（新增画面重合 3 项 + 关闭按钮 1 项） | 0 |
| `tools/smoke_exit.py`（新） | 2/2（真实 `app.main()` 退出路径，连跑两次） | 0 |
| `tools/smoke_phase2.py` | 19/19 | 0 |
| `tools/smoke_containers.py` | 9/9 | 0 |
| `tools/smoke_playback.py` | OK（真实录像 + d3d11va） | 0 |

所有测试脚本现在都走与正式程序一致的退出路径，因此"退出码是否为 0"本身也成了一个断言。

## 7. 遗留

- 「锯齿感」需用户复听确认；目前只有缓解与取证，没有定论。
- 单引擎架构（一个引擎 + 标签只保存播放状态）能彻底消除多实例问题，但改动面大，
  当前"每标签一引擎 + park 释放音频"已实测稳定，暂不重构。
- `MAX_TABS=6` 仍为经验值。
