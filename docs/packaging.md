# 打包成 Windows exe — 方案

> 状态：**方案已定，尚未执行**。等 Phase 3（录屏）完成后再动手，
> 避免每改一次功能就重新打一次 300 MB 的包。
>
> 本文里的体积数字都是本机实测（`vendor/`、`.venv/` 实际字节数），
> 命令与配置是**待验证**的草案，执行时会逐条实测并在本文档回填结果。

## 1. 目标与约束

| 项 | 要求 |
| --- | --- |
| 形态 | 双击即用的 Windows 11 桌面程序，**不需要装 Python** |
| 体积 | 越小越好，但要能离线播放（libmpv 必须随包） |
| 数据 | 仍然只写 `%LOCALAPPDATA%\ReplayStudio\`，不碰 ACLOS 原始文件 |
| 更新 | 不能因为改一行 Python 就重打 300 MB → 优先 onedir |
| 合规 | 分发二进制必须带许可证清单（见第 6 节，**这是硬约束**） |
| 未签名 | 没有代码签名证书，SmartScreen 会拦一次；要在文档里说明 |

## 2. 现状盘点（实测）

| 内容 | 体积 | 是否必须随包 |
| --- | --- | --- |
| `src/`（我们的代码） | 0.6 MB | 是 |
| `vendor/mpv/libmpv-2.dll` | 115 MB | **是**，没有它不能播放 |
| `.venv/Lib/site-packages/PySide6` | **647 MB** | 是，但可以砍掉一大半 |
| `vendor/obs/`（OBS 便携版解压后） | 466 MB | 否，Phase 3 才用，建议做成可选 |

PySide6 那 647 MB 里最大的是我们用不到的东西：

```
194.1 MB  Qt6WebEngineCore.dll      ← 没用到
 19.7 MB  opengl32sw.dll            ← 软件渲染回退，没用到
  6.4 MB  Qt6Quick.dll / 6.2 Qt6Qml ← 没用到
  5.0 MB  Qt6Designer.dll           ← 没用到
  4.4 MB  Qt6Pdf.dll                ← 没用到
```

全项目只 import 了三个 Qt 模块：

```
$ grep -rhoE "from PySide6\.[A-Za-z0-9]+" src/ tools/ tests/ run.py | sort | uniq -c
     27 from PySide6.QtCore
     26 from PySide6.QtWidgets
      8 from PySide6.QtGui
```

**结论：把 PySide6 换成 PySide6-Essentials 是最有效的一步**（见 4.1）。

## 3. 工具选型

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| **PyInstaller onedir** | 生态成熟、Qt hook 现成、增量改动不用重打全部 | 目录形态（不是单文件） | ✅ 选它 |
| PyInstaller onefile | 单 exe 好看 | 每次启动把 115 MB dll 解压到 `%TEMP%`：**启动慢数秒**、易被杀软拦截、多实例各解一份 | ❌ |
| Nuitka | 体积略小、启动略快 | 编译耗时久（本项目 20+ 分钟），调试成本高 | ❌ 收益不抵成本 |
| `pyside6-deploy` | 官方 | 本质也是打包 Nuitka/PyInstaller，且默认假设移动端 | ❌ |
| cx_Freeze / py2exe | 可用 | Qt hook 不如 PyInstaller 完整 | ❌ |

onedir 的"缺点"可以用一个 7z 自解压包 / Inno Setup 安装器补上用户体验，
这一步留到最后做（压缩后预计 100 MB 上下）。

## 4. 体积优化（按收益排序）

### 4.1 依赖换 PySide6-Essentials（预计省 400 MB+）

`requirements.txt` 里的 `PySide6` 是**全量包**（含 WebEngine / Quick / 3D / Pdf / Designer）。
改成 `PySide6-Essentials`（QtCore/Gui/Widgets/Network/… 不含 Addons）：

```
PySide6-Essentials==6.12.0
```

- 需要确认 6.12.0 有对应的 Essentials 版本（pypi 上 Essentials 与全量同步发布，**待验证**）
- 改完必须重跑全部 smoke 测试（当前 venv 会把 Addons 一起带上，换包后要确认没有隐性依赖）
- 这一条同时也会让日常开发环境的 venv 瘦身

### 4.2 exclude 明确用不到的 Qt 模块

即使换了 Essentials，PyInstaller 的 hook 仍会收集部分插件，显式排除更稳：

```
--exclude-module PySide6.QtWebEngineCore
--exclude-module PySide6.QtWebEngineWidgets
--exclude-module PySide6.QtQml
--exclude-module PySide6.QtQuick
--exclude-module PySide6.QtQuick3D
--exclude-module PySide6.QtPdf
--exclude-module PySide6.QtDesigner
--exclude-module PySide6.Qt3DCore
```

### 4.3 测试代码不进包

`tests/`、`tools/`、`_ref/`、`docs/` 都不随包 —— 入口只依赖 `src/` 与 `run.py`，
天然满足，不需要额外配置。

### 4.4 OBS 做成可选

`vendor/obs/` 466 MB，且只有 Phase 3 用得到。两种做法：

- **A（推荐）**：主包不含 OBS；Phase 3 首次打开录屏页时提示"下载/解压 OBS 便携版"，
  程序本体保持 300 MB 以内
- **B**：打包时若 `vendor/obs/obs64.exe` 存在就 `--add-data` 一起打进去，
  产出一个 800 MB 的"完整版"

倾向 A，等 Phase 3 定稿后再定。

## 5. 必须改的代码（冻结后失效的地方）

打包不是纯配置活，下面两处代码现在假设"源码目录结构"，冻结后会失效：

### 5.1 `media/player.py::ensure_mpv_dll`

现在用 `__file__` 往上四层拼 `vendor/mpv`：

```python
_MPV_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "vendor", "mpv")
```

冻结后 `__file__` 指向临时解包目录，这条路不存在。需要加冻结分支：

```python
if getattr(sys, "frozen", False):          # PyInstaller
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    _MPV_DIR = os.path.join(base, "vendor", "mpv")
```

### 5.2 OBS 路径解析（Phase 3 引入时）

同理，`vendor/obs/` 的定位也要走同一套 `frozen / _MEIPASS` 判断。
建议在 `core/` 里收一个 `runtime_paths.py` 统一出口，避免两处各写一遍。

### 5.3 入口与图标

- 入口：`run.py` 保持不变（它已经只做 `sys.path` + `main()`）
- 图标：仓库里还没有 `.ico`。用 FFmpeg 从 `docs/screenshots/` 里挑一张转成
  `resources/icon.ico`（`ffmpeg -i in.png -vf "scale=256:256" resources/icon.ico`）
- 版本信息：`--version-file` 需要一个 VERSIONINFO 文本文件，打包时生成

## 6. 许可证合规（硬约束，不能省）

分发二进制 = 分发别人的库，必须随包带许可证。当前依赖里有 GPL/LGPL 成分：

| 组件 | 许可 | 分发要求 |
| --- | --- | --- |
| libmpv / mpv | **GPLv2+**（也可按 LGPL 取，取决于构建） | 必须提供源码或**书面要约**（三年有效），并附许可证全文 |
| OBS Studio | **GPLv2** | 同上（若随包分发） |
| PySide6 / Qt | **LGPLv3**（另有 GPL/商业双许可） | 附 LGPL 文本 + 说明动态链接方式 + 提供 Qt 源码获取途径 |
| FFmpeg（仅外部调用） | LGPL/GPL 视构建而定 | 我们不分发 FFmpeg 二进制，只在文档里说明依赖 |
| Python / cryptography 等 | PSF / Apache-2.0 等宽松许可 | 附文本即可 |

落地做法：打包产物根目录放 `LICENSES/`：

```
LICENSES/
  ReplayStudio.txt        # 本项目许可（待定，暂按源码随附处理）
  mpv-GPLv2.txt
  OBS-GPLv2.txt           # 仅当 OBS 随包
  Qt-LGPLv3.txt
  THIRD-PARTY-NOTICES.md  # 版本、来源 URL、源码获取方式
```

> ⚠️ 这一项在真正对外发布前必须由人复核一遍。本文只记录要求，不构成法律意见。

## 7. 打包流程（草案，执行时逐条实测）

```powershell
# 1) 干净的构建环境
.venv\Scripts\pip install pyinstaller

# 2) 生成 spec（只做一次，之后手工维护）
.venv\Scripts\pyi-makespec --noconsole --onedir --name ReplayStudio `
    --icon resources\icon.ico `
    --add-binary "vendor\mpv\libmpv-2.dll;vendor\mpv" `
    --exclude-module PySide6.QtWebEngineCore `
    --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtPdf `
    --exclude-module PySide6.QtDesigner `
    run.py

# 3) 构建
.venv\Scripts\pyinstaller ReplayStudio.spec --noconfirm

# 4) 产出
dist\ReplayStudio\ReplayStudio.exe
```

spec 里还要手工确认/补充：

- `datas`：`resources/`（图标与静态资源）
- Qt 插件：PyInstaller 的 PySide6 hook 会自动收 `platforms/qwindows.dll`、
  `styles/`、`imageformats/`；构建后要**逐个检查有没有漏**（漏了会白屏）
- 不做 UPX 压缩：115 MB 的 dll 压缩收益有限，反而更容易触发杀软误报

同时建议产出**两个** exe：

| 文件 | 控制台 | 用途 |
| --- | --- | --- |
| `ReplayStudio.exe` | 无（`--noconsole`） | 日常使用 |
| `ReplayStudio-debug.exe` | 有 | 出问题时的取证入口（对应现在的 `debug.bat`） |

## 8. 验证清单（打包后逐条打勾）

- [ ] 在没有 Python 的机器上双击能起来（至少用一台没装 Python 的机器/虚拟机）
- [ ] 冷启动耗时可接受（目标 < 5 s；onefile 做不到，所以选 onedir）
- [ ] 播放真实录像：画面居中、有声、进度可拖、连播正常
- [ ] 无畏时刻库能扫到、能筛选、能回看
- [ ] 主题切换正常（QSS 是运行时拼的字符串，理论上与打包无关）
- [ ] 关闭应用**不崩溃**（`tools/smoke_exit.py` 的思路要在 exe 上重跑一遍；
      libmpv 的 DLL detach 问题不受打包影响，但要确认 `hard_exit` 在冻结环境仍生效）
- [ ] 日志目录仍能写：`%LOCALAPPDATA%\ReplayStudio\logs\`
- [ ] 中文/空格路径的视频能打开
- [ ] 卸载方式明确（onedir 直接删目录；若做安装器则提供卸载入口）
- [ ] `LICENSES/` 齐全

## 9. 未决问题

1. 是否做安装器（Inno Setup / 7z 自解压）？不做的话用户拿到的是一个文件夹
2. 是否随包分发 OBS（466 MB）还是首次使用时下载？
3. 自动更新做不做？不做的话，更新 = 重新下载整个包
4. 本项目自身的开源许可还没定，需用户拍板
