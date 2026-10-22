# Devlog 010 — 数据库性能优化：604 ms → 4.3 ms

日期：2026-10-23
输入：「想办法优化一下数据库的性能」。

## 1. 先测，别猜

写了 `tools/probe_db_perf.py`，把浏览时会用到的每个调用各跑 5 次取最快：

```
query_videos(page0, no filter)     67.70 ms
facets() (no filter)              347.54 ms
stats()                            73.10 ms
list_accounts()                    69.96 ms
one short-lived connection         69.93 ms      <-- 连 SELECT 1 也是这个数
ONE FULL PAGE REFRESH             604.35 ms
```

`SELECT 1` 和 `stats()` 一样贵，说明时间不在 SQL 上。继续拆：

```
connect only                        0.94 ms
connect + SELECT 1  (AppData, WAL) 61.27 ms
connect + SELECT 1  (副本在 TEMP)   1.91 ms
connect + SELECT 1  只读 URI        2.94 ms
SELECT 1 on existing connection     0.00 ms
```

**开销 96% 是"建立读写连接"本身**，不是查询。同一份数据放到别处只要 1.9 ms，
所以也不是数据规模问题；这台机器上每次以写意图打开就得 ~60 ms。

而 `facets()` 里 `group()` 每个维度各 `with self.conn()`，加一次 total/missing，
**一次调用开了 5 次连接**。

## 2. 做了什么

- `Database.read_conn()`：只读 URI（`file:...?mode=ro`，WAL 无 `-shm` 时回退
  读写）+ PRAGMA 调优。
- 只读连接**常驻复用**（实例级单条 + `RLock` + `check_same_thread=False`）：
  每次调用的查询成本本来就 0.2–0.6 ms，每次开连接再便宜也还有 3 ms × 5 次。
  复用前用一条免费的 `SELECT 1` 验证句柄，坏了就重开；`weakref.finalize`
  负责回收，避免句柄泄漏导致文件删不掉（Windows 上会真的删不掉）。
- `facets()` / `stats()` / `query_videos()` 等全部改成单次调用单连接。
- `refresh_file_states()` 去掉 N+1（每行一次 SELECT → 一次取 dict）。
- `local_scanner` 每 500 行提交：整棵 `os.walk` 一个事务会让 SQLite 无法
  checkpoint，长扫描期间 WAL 一直涨。

## 3. 试了但没采用

- `ANALYZE` / `sqlite_stat1`：三种主查询耗时前后完全一致（0.49 / 0.18 / 0.34 ms），
  数据量远没到需要统计信息纠偏。
- `(file_state, created_ms)` 复合索引：`file_state != 2` 命中 70% 的行，
  规划器照样全表扫描，零收益。**没有为了"看起来优化过"把它留下。**

## 4. 结果

| 操作 | 前 | 后 |
|---|---|---|
| `query_videos(page0)` | 67.7 | 2.0 ms |
| `facets()` | 347.5 | 1.9 ms |
| `stats()` | 73.1 | 0.35 ms |
| `list_accounts()` | 70.0 | 0.02 ms |
| 一次完整页面刷新 | 604.3 | **4.3 ms** |

## 5. 防回归

`tools/smoke_db_perf.py`（9 项断言）：连接复用、只读连接拒绝写入、句柄损坏自愈、
写入对常驻读者可见、8 线程安全、临时库可删、各项耗时上限。

回归：smoke_core / tabs 25/25 / playback / containers 9/9 /
hidden_missing 11/11 / gui / phase2 19/19 / exit 2/2 / diagnostics 3/3 /
stuck_fix 全绿；真实 `run.py` 启动 9 秒无异常日志。
