# 开发日志 000：项目初始化与环境调查（Phase 0）

- 日期：2026-10-19
- 提交：`8e19ce3`

## 1. 环境调查结论

| 项 | 结果 |
| --- | --- |
| OS | Windows 11 x64，NVIDIA GPU（CUDA 12.4） |
| Python | 3.13（managed）+ conda graph 环境；项目用独立 `.venv` |
| Node | 22 ✅ / Rust、.NET SDK ❌ |
| FFmpeg | 8.1（Scoop）✅，可硬编 h264_nvenc |
| OBS Studio | ❌ 未安装（Phase 3 前置条件） |
| ACLOS 数据 | ✅ `%APPDATA%/ACLOS/WonderfulDb`，3 账号 72 MB |
| 视频数据 | ✅ `D:\Document\Video\Valorant`，143 GB 实际存在 |
| GitHub 直连 | ❌ 超时，走本机代理 `127.0.0.1:7897` |

## 2. 三个高风险点最小验证

1. **ACLOS 解密** → `tools/probe_aclos.py`：解出 605 对局/1471 视频。✅
2. **libmpv + Qt 播放** → `tools/probe_mpv.py`：1080p60、d3d11va 硬解、seek 精确。✅
   - 坑：python-mpv 用 `%PATH%` 找 `libmpv-2.dll`，必须 `os.environ["PATH"]` 前插 vendor 目录。
3. **OBS 连接** → 未验证（OBS 未安装）。⏸

## 3. 数据格式调查（ACLOS / 无畏时刻）

- 数据库：`WonderfulDb`，内容为 hex 文本；解码后 AES-256-CBC，key=SHA256(openid)。
- 视频：`D:\Document\Video\Valorant\wonderfulVideos<openid>\<match-uuid>\<md5>.mp4`，
  同目录常有 `cover-*.jpeg` 官方渲染封面（69% 存在）。
- 区分：这些是**普通视频**，没有 Riot Replay 的自由视角能力，不会虚假实现。
- 官方清理过的 435 个文件在库中标记"源文件缺失"而非删除记录，用户可自行清理索引。

## 4. 产出

- `docs/architecture/decisions/decision-001.md`（ADR：技术栈选型）
- 项目骨架 `src/replaystudio/{aclos,core,media,ui}`，`run.py` 入口
- 决策：**先交付 Phase 1（无畏时刻库）**，通用播放器与录屏随后。
