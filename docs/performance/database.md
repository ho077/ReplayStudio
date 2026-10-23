# 性能

## 退出耗时（2026-10-24，见 devlog 011）

| 场景 | 优化前 | 优化后 |
|---|---|---|
| 0 个标签（没用过录屏） | 285 ms | 3 ms |
| 2 个标签 + 缩略图任务 | 416 ms | 81 ms |
| OBS 正在运行 | 最多 25 s | 2.1–2.6 s |

主因：退出时 `obs_launcher.stop()` 会 `subprocess` 起一个 `tasklist` 去问
"OBS 在不在"，约 250 ms —— 哪怕整个会话根本没打开录屏页。修法：没碰过 OBS
就不收；碰过就查记住的 PID（ctypes，0 成本，全死了才回退 tasklist）；
并给退出设 3 秒总预算（原来三阶段各等各的，合计 25 秒）。缩略图线程池从
"等 3000 ms" 改成清队列 + 250 ms 宽限。复跑：`tools/probe_shutdown.py`、
`tools/probe_obs_quit.py`。

---

# 数据库性能

数据规模（2026-10-23 实测）：`index.db` 2.5 MB，`videos` 1472 / 可见 1037，
`matches` 606，`events` 7949，`play_history` 462。以下数字全部来自
`tools/probe_db_perf.py` 与 `tools/smoke_db_perf.py`，可复跑。

## 结论：慢的不是 SQL，是"建立连接"

查询本身在毫秒以下：

| 查询 | 耗时 |
|---|---|
| 主列表 `ORDER BY created_ms DESC LIMIT 200`（含 JOIN） | 0.49 ms |
| `COUNT(*)`（同 WHERE） | 0.18 ms |
| 分类聚合 `GROUP BY map_cn` | 0.34 ms |

而打开一次连接：

| 方式 | 耗时 |
|---|---|
| 读写打开 + `SELECT 1` | **61 ms** |
| 只读打开（`file:...?mode=ro`）+ `SELECT 1` | 3 ms |
| 已打开的连接上再执行一条语句 | ~0 ms |

即：**每次翻页/筛选的开销有九成以上是打开文件，不是查数据。**
本机上读写打开的额外代价推测来自每次写意图打开时的系统扫描/共享内存初始化
（同一份数据库复制到临时目录只需 1.9 ms，说明不是数据本身）。

## 采取的措施

1. **查询一律走只读连接** `Database.read_conn()`（`_ro_uri` 构造 `file:` URI）。
   WAL 数据库在没有 `-shm` 时无法只读打开，因此保留了读写回退。
2. **只读连接常驻复用**（一个 Database 实例一条，`threading.RLock` +
   `check_same_thread=False`，因为是多线程共享）。每次复用前用一条
   `SELECT 1` 验证句柄有效（0 ms）；句柄坏掉（重建索引换文件、扫描重做 -shm）
   时自动丢弃重开。`weakref.finalize` 在对象被回收时关闭句柄，避免"文件被占用"、
   临时库删不掉。
3. **一次调用只用一次连接**。`facets()` 原来开 **5 次**（4 个维度各一次 +
   total/missing 一次），`stats()`、`query_videos()` 同理；现在整段查询共用
   同一个 `read_conn()` 作用域。
4. `refresh_file_states()` 去掉 N+1：原来每行一次 `SELECT file_state`，改为
   一次取出 dict（1471 条语句 → 1 条）。注意它的耗时仍主要是 1472 次 `os.stat`
   （约 77 ms），这部分换不掉。
5. 文件夹扫描（`local_scanner`）每 500 行提交一次。原来整个 `os.walk` 包在
   一个事务里，期间 SQLite **无法 checkpoint WAL**，长扫描会让 `-wal` 一直涨，
   且中途/异常退出全部回滚。

## 验证：没有做的两件事

- 没加 `sqlite_stat1`（`ANALYZE`）：实测三种主查询耗时完全不变（0.49/0.18/
  0.34 ms，前后一致），因为数据量远没到需要统计信息纠偏的程度。
- 没加 `(file_state, created_ms)` 复合索引：`file_state != 2` 命中 70% 的行，
  规划器仍选择全表扫描，实测零收益。

## 结果

| 操作 | 优化前 | 优化后 |
|---|---|---|
| `query_videos(page0)` | 67.7 ms | **2.0 ms** |
| `facets()` | 347.5 ms | **1.9 ms** |
| `stats()` | 73.1 ms | **0.35 ms** |
| `list_accounts()` | 70.0 ms | **0.02 ms** |
| **一次完整页面刷新**（以上四项合计） | **604 ms** | **4.3 ms** |

`tools/smoke_db_perf.py` 把这些不变量固化了：连接被复用、只读连接拒绝写入、
句柄坏了能自愈、写入对常驻读者可见、8 线程并发安全、临时库可删除，
以及各项耗时上限。改动任何一项都会让测试失败。
