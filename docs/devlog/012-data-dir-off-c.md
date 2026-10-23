# 012 · 把数据目录从 C: 挪到 D:

日期：2026-10-23

## 起因

用户问「OBS 录得视频全放在 C 盘了吗」，顺带问「我的 C 盘为什么只有 36 GB」。查下来
`record_dir` 确实一直是 `%LOCALAPPDATA%\ReplayStudio\recordings`（已在上一次提交里修掉），
但同一趟也暴露了第二个静默的 C: 消耗点：**应用自己的数据目录也在那里**。

```
C:\Users\Admin\AppData\Local\ReplayStudio\     82 MB
  index.db                                    2.6 MB
  thumbs\                      缩略图缓存，只会长不会缩
  logs\                        faulthandler / session 日志
  recordings\                  旧的录屏默认目录
```

82 MB 本身不是 36 GB 的主因，但它是唯一一个「每用一天就更大一点」的目录，而且用户
明确说过不想让生成的文件落在 C:。

## 做法

`core/db.py::user_data_dir()` 从「写死 LOCALAPPDATA」改成三层：

1. `REPLAYSTUDIO_DATA_DIR` 环境变量 —— 最高优先级。**这是给冒烟测试用的**：它们以前
   一直读写真实索引库，测试跑一次就往里写一次数据，这本身是个隐患。
2. 否则 `<repo>/data` —— 和代码在一起。本机解析为
   `D:\WorkSpace\WorkBuddySpace\apps\ReplayStudio\data`。
3. 都没有且 2 不可写（比如装进 Program Files）时才退回旧的 LOCALAPPDATA。

迁移写成 `_migrate_legacy_data(old, new)`，规则刻意保守：

- **只复制，不删除**。升级不该顺手删用户数据；旧目录留着，用户在设置页打开新目录
  确认无误后自己删。
- 新目录已有 `index.db` 就立刻返回 —— 否则每次启动都会重复制一遍 82 MB。
- 跳过 `open_req_*`（单实例的文件投递暂存），那是每次启动的临时物，不是数据。
- 整个函数包在 `try/OSError` 里：迁移失败只是「用新目录重建索引」，不该让启动挂掉。
- **显式设了环境变量时不迁移**，否则测试会把真实索引复制进临时目录。

`core/diagnostics.py::log_dir()` 之前自己拼 `%LOCALAPPDATA%`，跟着改成走
`user_data_dir()`，不然日志会留在 C: 上。

`player_page.take_screenshot()` 之前写 `~\Pictures\ReplayStudio`（同样在 C:），而
`project-status.md` 一直写着「截图在数据目录里」—— 文档和代码早就对不上，这次统一成
`user_data_dir()/screenshots`。

`.gitignore` 加 `/data/`：索引库里有账号 openid、缩略图有画面内容，都不该进版本库。

## 验证

`tools/probe_data_dir.py`（新增）三段检查：

```
== 1. env override ==
  [ok ] env wins
  [ok ] no migration under an explicit override
== 2. migration from a fake legacy folder ==
  [ok ] reported a move / index.db copied / subfolder copied
  [ok ] hand-off scratch skipped / source left alone
  [ok ] second run is a no-op
== 3. the real default on this machine ==
  default : D:\WorkSpace\WorkBuddySpace\apps\ReplayStudio\data
  legacy  : C:\Users\Admin\AppData\Local\ReplayStudio
  videos  : 1037
  logs    : D:\...\data\logs
```

回归全绿：`smoke_core`、`smoke_db_perf`（一次刷新 4.24 ms）、`smoke_gui`、
`smoke_phase2` 19/19、`smoke_record_ui` 35/35、`smoke_tabs` 25/25、`smoke_exit` 2/2。

## 留下的坑

- 旧目录 `C:\Users\Admin\AppData\Local\ReplayStudio`（82 MB）**没有被删**，需要用户
  确认新目录正常后手动删。
- 打包成 exe 后 `<repo>/data` 会变成 exe 同级目录。写在 Program Files 时会落到第 3 条
  兜底路径，Phase 5 做打包时要重新决定（更可能是 `Documents\ReplayStudio`）。
- 缩略图缓存没有上限，长期用会一直涨。现在是 80 MB 量级，等它到 GB 级再加 LRU。
