# 已知问题与故障排查

## 环境类

### `RuntimeError: 未找到 libmpv-2.dll`
确认 `vendor/mpv/libmpv-2.dll` 存在（115 MB）。python-mpv 通过 `%PATH%` 找它，
`media/player.py::ensure_mpv_dll` 已自动处理；若手动跑脚本，先 `import replaystudio.media.player`。

### GitHub 直连超时
本机走代理 `http://127.0.0.1:7897`：`git -c http.proxy=... clone ...`。

## 数据类

### 显示"源文件缺失"
435 条记录对应的 mp4 已被腾讯客户端清理。数据真实状态。可在设置页"重建索引"后
配合网格右键或"仅缺失文件"筛选处理；本项目绝不主动删除任何源文件。

### 特工/地图显示为英文 ID
中文名映射来自官方本地化表；未收录的新特工/新地图会回退显示原始名称（刻意不编造）。
更新 `aclos/taxonomy.py` 后重建索引即可。

## 播放类

### 续播报 mpv -12（已修复，留档）
`loadfile` 后立刻 `seek` 有竞态；现用 `start` 选项。若再遇 -12，检查是否在
file-loaded 之前就发命令。

### 截图工具抓不到视频画面
mpv 渲染在原生子窗口（`wid`），`win.grab()` 抓不到其内容——播放本身正常，
看时间轴走动即可确认。

## Qt/PySide6 类（踩坑留档）

- `QImage.scaled()` 关键字参数是 `aspectMode`，**没有** `aspectRatioMode`；
  传错会在后台线程抛 AttributeError 被吞掉。一律用枚举：
  `Qt.AspectRatioMode.KeepAspectRatio`。
- `QPainter` 没有 `setClipped()`，是 `setClipping(False)`。
