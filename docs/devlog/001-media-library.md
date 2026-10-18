# 开发日志 001：媒体索引与无畏时刻库（Phase 0 + Phase 1）

- 日期：2026-10-19
- 提交：`8e19ce3`（Phase 0+1 全量首提交）

## 1. 本次解决什么问题？

把用户本机 270.5 GB、1471 个「无畏时刻」片段变成一个可筛选、可搜索、可播放、
标签可持久化的本地高光库，且绝不触碰 ACLOS 原始数据。

## 2. 研究了哪些方案？

- **WonderfulUI（GPL-3.0）**：完整阅读其 `docs/ACLOS_FORMAT.md` 与
  `src-tauri/src/parser/*`。ACLOS 数据库是 hex 文本，解码后为 AES-256-CBC 密文，
  密钥 = SHA256(openid)。其 Rust 实现验证了格式，但直接复用会把整个项目拖入 GPL。
- **自研解析层**：格式已文档化（IV 前 16 字节 + 密文），Python `cryptography` 十几行即可。

## 3. 最终选择与理由

自研 Python 解析层（`src/replaystudio/aclos/crypto.py` + `reader.py`）。
仅参考格式知识，不复制代码，规避 GPL 传染；且与后续 Qt 界面同语言，无 FFI 边界。

## 4. 关键文件

| 文件 | 职责 |
| --- | --- |
| `aclos/crypto.py` | hex 解码 + AES-256-CBC 解密（SHA256(openid) 派生密钥） |
| `aclos/reader.py` | 解析对局/视频/事件/快照昵称，产出强类型模型 |
| `aclos/taxonomy.py` | 29 特工 / 26 地图 / 15 模式的英文→简中映射 |
| `core/db.py` | SQLite WAL 库：accounts、matches、videos、events、tags、history |
| `core/scanner.py` | 后台线程增量扫描（mtime+size 变化检测，0.89 s 全量建索引） |
| `ui/moments_page.py` | 账号/地图/特工/类型/模式/结果 六维筛选 + 搜索 |
| `ui/grid.py` | model/view 虚拟化网格 + 分页加载 |

## 5. 核心原理：加密格式

```text
 WonderfulDb 文件(hex 文本) ──bytes.fromhex──► 二进制
        前 16 字节 = IV，其余 = AES-256-CBC 密文
        key = sha256(openid.encode()).digest()   # openid 来自目录名
```

解密后是 JSON。事件表每条含 `KillerIsMe`/`KilledIsMe`/武器/回合/毫秒时间戳，
这是"击杀时间轴点击跳转"功能的数据来源。

## 6. 遇到的问题与定位

### 6.1 特工筛选全错（数据语义 Bug）

现象：一个 `agent_id` 映射出 30 个不同特工。
定位：SQL 分组发现 `agent_id` 在同一账号内**恒定**——它是账号绑定的"本命特工" UUID，
每场实际使用的特工在 `agent_name`（英文）字段。
修复：`taxonomy.agent_display()` 改为优先用 `agent_name` 查中文名，UUID 仅作兜底。

### 6.2 缩略图永远"载入封面…"（PySide6 API 坑）

现象：网格请求反复发起、后台任务成功产出 JPEG，但 `done` 信号从未送达——
实际是每个任务都在 `QImage.scaled()` 抛 `AttributeError`。
PySide6 的参数名是 `aspectMode`，不是 `aspectRatioMode`（Qt 文档沿用的 C++ 名也不对，
必须是 `Qt.AspectRatioMode.KeepAspectRatio` 位置传参或 `aspectMode=`）。
异常被 `except Exception` 吞掉转成 failed，导致"静默全失败"。
教训：**后台线程里的宽泛 except 必须留日志**，否则错误不可见。

### 6.3 续播报 mpv -12（竞态）

`loadfile` 后立刻 `seek`，文件尚未加载完，mpv 返回 `MPV_ERROR_INVALID_PARAMETER`。
修复：改用 `start` 选项——它在**下一次 loadfile 生效**，天然无竞态：
`self._mpv.start = f"{start_pos}"; self._mpv.play(path)`。

### 6.4 委托裁剪泄漏

`QPainter.setClipPath` 后调用了不存在的 `setClipped()`（应为 `setClipping(False)`）。
缩略图修好后绘制路径才第一次真正跑到这里——**修复一个 Bug 常暴露下一个**。

## 7. 验证

| 命令 | 结果 |
| --- | --- |
| `tests/smoke_core.py` | 605 对局 / 1471 片段 / 7944 事件入库，索引 0.89 s，特工/模式分布正确 |
| `tools/smoke_gui.py` | 筛选(映射→4)、搜索(特工→128)、导航、缩略图缓存 全过 |
| `tools/smoke_playback.py` | 真实文件播放 + 硬解 d3d11va + seek 回跳 + 续播起点 正确 |
| `tools/screenshot.py` | 三页截图确认视觉与真实渲染（`docs/screenshots/`） |

**未验证项**：长时间大批量扫描（>10 万文件）、4K/HEVC 素材、多显示器 DPI。

## 8. 下一步优化方向

- 缩略图生成批量化：目前首屏 24 张 poster 复制几乎瞬时，FFmpeg 兜底帧较慢，
  可在扫描线程预生成热门条目。
- 事件列表大量击杀时的虚拟化（当前直接铺 QPushButton，>200 条应换 ListView）。
