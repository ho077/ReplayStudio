# 学习 01：ACLOS 数据是怎么被读出来的？

对应代码：`src/replaystudio/aclos/`（共 3 个文件，都值得通读）。

## 1. 无畏时刻的数据在哪里？

- 数据库：`%APPDATA%\ACLOS\WonderfulDb\`（每账号一个文件，文件名含 openid）。
- 视频：`D:\Document\Video\Valorant\wonderfulVideos<openid>\<对局uuid>\<哈希>.mp4`。
- 封面：视频同目录的 `cover-*.jpeg`，是游戏客户端渲染好的。

## 2. 为什么文件不能直接打开？

腾讯把数据库内容做了两层保护：

1. **hex 编码**：文件里全是 `0-9a-f` 字符，`bytes.fromhex()` 还原成二进制。
2. **AES-256-CBC 加密**：密钥不是存在哪，而是**派生**出来的——
   `sha256(openid字符串).digest()`。openid 就在文件路径里，所以"钥匙挂在门上"，
   这是防盗君子设计。解密格式：前 16 字节是 IV，其余是密文。

看 `crypto.py::decrypt_file`，一共 10 行。做个小实验：

```python
from replaystudio.aclos.crypto import decrypt_file
import json, glob, os
f = glob.glob(os.path.expandvars(r"%APPDATA%\ACLOS\WonderfulDb\*"))[0]
data = decrypt_file(f, openid=os.path.basename(f).split("_")[-1].split(".")[0])
doc = json.loads(data)
print(type(doc), list(doc)[:10])
```

> 注意：openid 的提取方式以 `reader.py::discover_accounts` 为准，上面是示意。

## 3. 解密后的 JSON 里有什么？

| 键 | 内容 | 用途 |
| --- | --- | --- |
| 对局列表 | mapName、agentName、KDA、是否胜利、回合比分 | 筛选维度 + 对局信息面板 |
| 视频列表 | 路径、时长、分辨率、类型(三杀/五杀…)、创建时间 | 网格卡片 |
| 事件 | 每次击杀的毫秒时间戳、武器、KillerIsMe | 时间轴红点 + 点击跳转 |

## 4. 本项目的分层

- `crypto.py` 只管"字节 → JSON"。
- `reader.py` 只管"JSON → 数据模型"，含快照里的历史昵称（改名也不丢）。
- `taxonomy.py` 只管"英文 ID → 中文名"。

**动手任务（入门）**：改 `taxonomy.py` 里 `MODE_CN_BY_EN`，加一个自定义别名，
重新扫描后看筛选下拉框变化。
**进阶任务**：在 `reader.py` 里找 `KillerIsMe` 的用法，解释为什么"被击杀"事件也有价值。
**挑战任务**：给 `events` 表加一种新事件类型（例如助攻），需要同时改 reader、db、UI 三层。
