# Devlog 008 — 全屏是个空操作，分类括号里的数字在撒谎

- 日期：2026-10-22
- 触发来源：用户实际使用反馈「软件全屏还有问题，全屏后好像还会闪退」「按照地图或英雄
  分类时，实际数量不到分类括号后面带的数量」
- 涉及文件：
  - `src/replaystudio/ui/player_page.py`（全屏改走 Qt；补上双击画面；`release()` 先落回窗口）
  - `src/replaystudio/ui/main_window.py`（`set_fullscreen()` 收起外壳；Esc 先退出全屏）
  - `src/replaystudio/ui/player_tabs.py`（`set_fullscreen()` 转发给所有标签页）
  - `src/replaystudio/core/db.py`（抽出 `_build_where(f, skip)`；`facets()` 带上其他筛选）
  - `src/replaystudio/ui/moments_page.py`（每次 `reload()` 原地刷新计数）
  - 新增探针：`tools/probe_fullscreen.py`、`probe_facet_counts.py`、`probe_facet_ui.py`

## 1. 全屏：mpv 的 `fullscreen` 属性在嵌入模式下什么都不做

原来的实现是：

```python
self.player.set_fullscreen(self._fs)      # -> mpv_set_property("fullscreen", …)
```

mpv 的 `fullscreen` 属性是给**它自己创建的窗口**用的。本项目是嵌入模式（`wid=`
指向 Qt 给的 HWND），那个窗口根本不存在。用 `tools/probe_fullscreen.py mpv` 实测：

```
mode=mpv rounds=4
  host before: (662, 276, 1140, 841) popup=False
  round 0: want=on -> window normal, host (662, 276, 1140, 841) popup=False
  round 1: want=off -> window normal, host (662, 276, 1140, 841) popup=False
```

窗口没全屏，mpv 宿主 HWND 的位置尺寸也没变，`GWL_STYLE` 的 popup 位也没动 —— 四次
开关，一切照旧。也就是说按 F 之后**什么都没有发生**，用户看到的"全屏有问题"首先是
"全屏没反应"。

修复：交给 Qt，别让 libmpv 去动它不拥有的窗口。

- `MainWindow.set_fullscreen(on)`：`showFullScreen()`，同时收起标题栏 / 侧边栏 /
  状态栏（无边框窗口全屏后没有可见的退出入口，所以 Esc 必须先退出全屏，见下）；
- `PlayerTabs.set_fullscreen(on)`：藏标签条，并把状态转给**每一个**页面 —— 全屏期间
  新开的标签也得是一开场就全屏的；
- `PlayerPage.set_fullscreen(on)`：藏右侧信息面板，延时 80ms 重新对齐 mpv 宿主 HWND
  （画面尺寸变了，而那个 HWND 不跟布局走，见 devlog 005）；
- `PlayerPage.release()` 与 `MainWindow.navigate()` 都会先落回窗口状态，避免出现
  "没有侧边栏也没有标题栏的空白全屏"。

顺带补上一个**承诺了却没实现**的功能：按钮 tooltip 写着"全屏（F / 双击画面）"，
但双击从来没绑过。画面是 native window，鼠标事件被吞掉，所以用事件过滤器监听
`MouseButtonDblClick`。

Esc 的语义现在是分层的：全屏时先退出全屏，再按才关闭标签 —— 否则全屏状态下关掉
标签会留下一个外壳全部隐藏、无从下手的窗口。

## 2. 分类计数：括号里的数字从来没算过其他筛选条件

先跑 `tools/probe_facet_counts.py` 对一遍 SQL。裸筛选（只选地图）**完全一致**：
霓虹町 154 / 154，芮娜 148 / 148，一个不差。所以问题不在 SQL 本身。

一旦**同时**还有别的筛选，就对不上了：

```
### with {'mine_only': True} also active
  霓虹町        facet=154    query=153      <<< MISMATCH
  隐世修所       facet=130    query=129      <<< MISMATCH
  猎枭          facet=75     query=72       <<< MISMATCH
```

原因：`facets()` 只按 `openid` 和"文件未缺失"统计，而 `query_videos()` 套的是完整
筛选（账号、搜索框、仅收藏、仅我的击杀/死亡、胜负）。下拉框上写的是**全量**，点下去
拿到的是**筛选后**的结果 —— 用户看到的就是"实际数量不到括号里的数量"。

修复：把 WHERE 的构建抽成 `_build_where(f, skip)`，两个调用方共用。每个维度的计数用
"除自己以外的全部条件"来算：

```python
def group(col, own_key):
    where, args = self._build_where(f, skip=(own_key,))
```

这样"霓虹町 (153)"的含义就是"在你当前勾选的条件下，选它会看到 153 条"。这是分面
搜索（faceted search）的标准行为：计数随其他条件联动。

UI 侧配套两处：

- `reload()` 每次都刷新计数。若分组值没变，只改写标签文本（`setItemText`），不重建
  下拉框 —— 重建会在用户操作到一半时把选中项弄丢；
- 底部统计的"索引共 N 个"改用 `db.stats()`。它是索引的属性，不该跟着筛选变；而
  facet 计数恰恰相反，必须跟着变。

## 3. 验证

| 检查 | 结果 |
|---|---|
| `probe_facet_counts.py mine` | 修复前 7 处不符 → 修复后 0 处 |
| `probe_facet_counts.py win / fav` | 0 处不符 |
| `probe_facet_ui.py`（真实 MomentsPage，读控件上的数字） | 16 项全部 `label == grid == shown` |
| 分页（`三杀时刻 (686)`，超过单页 240） | 滚动加载后 `loaded == promised == 686` |
| `probe_fullscreen.py qt 8` | 8 轮进出全屏，退出后外壳 6 项全部复原 |
| `probe_fullscreen.py qt-stress`（全屏 + 播放 + 24 次切主题 + 关标签，×3 轮） | 3/3 通过 |
| 回归：`smoke_core / tabs(25/25) / playback / containers(9/9) / hidden_missing(11/11) / gui / phase2(19/19) / exit(2/2) / diagnostics(3/3)` | 全绿 |
| 回归：`probe_theme_leak(0/4 存活) / crash_bisect real+keep / theme_crash / mpv_lifecycle` | 全绿 |

## 4. 遗留与坦白

- **"全屏后闪退"没能复现**。`probe_fullscreen.py mpv-close`（旧实现全屏 → 关标签 →
  连切 24 次主题）跑了两次都没崩。旧实现的问题已经坐实的是"完全不生效"，而不是
  "必然崩溃"。用户遇到的闪退很可能仍是 devlog 007 里那条已知的
  `probe_theme_leak.py 4 12` 约 1/3 概率段错误（引擎反复创建/销毁那一类）。
  现在全屏是**真正生效**的新代码路径，已按上表压过，但请在真实使用中再试一次：
  日志在 `%LOCALAPPDATA%\ReplayStudio\logs\`。
- 全屏时窗口是 `Qt.FramelessWindowHint`，没有系统标题栏也没有任务栏入口。目前唯一
  出口是 F / Esc / 双击。若后续加"全屏时鼠标移动显示控制条"，记得同时保留这些出口。
- 分面计数现在会随搜索框变化，用户可能会觉得数字"在跳"。这是刻意的：跳动的数字
  等于"选下去就是这个数"，比一个永远对不上的静态数字诚实。
