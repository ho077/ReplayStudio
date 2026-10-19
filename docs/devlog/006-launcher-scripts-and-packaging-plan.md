# Devlog 006 — 双击启动脚本，以及打包 exe 的方案

- 日期：2026-10-20
- 触发来源：用户「写个 .bat 方便启动，等项目完善后打包成 exe 文件更好」
- 涉及文件：
  - 新增 `start.bat`（静默启动）、`debug.bat`（保留控制台的调试入口）、`setup.bat`（环境准备）
  - 新增 `.gitattributes`（`.bat` 强制 CRLF）
  - 新增 `docs/packaging.md`（打包方案，**尚未执行打包**）
  - 更新 `README.md`、`docs/project-status.md`、`docs/roadmap.md`

## 1. 为什么是三个而不是一个

双击启动这件事有两个互相矛盾的需求：**平时不要弹黑框**，**出问题时必须看得见报错**。
一个 bat 同时满足不了，所以拆成两个入口，再加一个只跑一次的环境准备脚本：

| 文件 | 干什么 | 什么时候用 |
| --- | --- | --- |
| `start.bat` | `pythonw.exe run.py`，不保留控制台 | 日常 |
| `debug.bat` | `python.exe run.py`，退出后 `pause`，打印退出码含义 | 闪退、白屏、报错 |
| `setup.bat` | 找 Python → 建 `.venv` → 装依赖 → 检查 `vendor/` | clone 之后第一次 |

`start.bat` 还会把 `%*` 转交给 `run.py`，所以从资源管理器「打开方式」过来的视频路径
（含空格的路径）一样能传进去 —— 实测 `start.bat one.mp4 "two words.mp4"` 参数完整到达。

## 2. 三个真实的坑（都是实测出来的，不是猜的）

### 2.1 `del` 会把 `%ERRORLEVEL%` 清零

`start.bat` 想在"程序启动瞬间就退出"时给用户一句提示。做法是先记 `pythonw.exe` 进程列表，
启动后再记一次，用 `fc` 对比：

```bat
fc "%BEFORE%" "%AFTER%" >nul 2>&1
del /q "%BEFORE%" "%AFTER%" >nul 2>&1
if not errorlevel 1 ( ...提示... )     ← 恒真，永远提示
```

第一次实测：**程序明明起来了，提示还是弹了**。原因是 `del` 执行成功会把 `ERRORLEVEL` 置 0，
`if not errorlevel 1` 于是永远成立。修法是先把结果存进变量再清理：

```bat
set "SAME=%ERRORLEVEL%"
del /q "%BEFORE%" "%AFTER%" >nul 2>&1
if "%SAME%"=="0" ( ...提示... )
```

顺带一提，为什么用"前后各抓一次进程列表"而不是 `tasklist | find "pythonw.exe"`：
只按进程名判断会把**别人残留的 pythonw 进程**也算进去。实测时就遇到一个上一轮测试
残留的 `pythonw.exe`（PID 41976），导致"程序已经死了"的判断被误判成"活着"。

### 2.2 `for /f` 里带引号的可执行文件会被 `cmd /c` 剥掉首引号

`setup.bat` 要打印找到的 Python 版本，最初写成：

```bat
for /f "delims=" %%v in ('%BASEPY% -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%v"
```

当 `%BASEPY%` 是带空格的绝对路径（`"D:\Anaconda3\python.exe"`）时报：

```
'D:\Anaconda3\python.exe" -c "import' 不是内部或外部命令
```

这是 `cmd /c` 的经典引号规则：整条命令以引号开头时，**第一个和最后一个引号会被吃掉**，
于是后面的参数全乱。改成先把结果写进临时文件再 `set /p` 读回来：

```bat
%BASEPY% -c "import sys;print(sys.version.split()[0])" > "%TEMP%\rs_pyver.txt" 2>nul
set /p PYVER=<"%TEMP%\rs_pyver.txt"
del /q "%TEMP%\rs_pyver.txt" >nul 2>&1
```

### 2.3 本机根本没有 `python` 在 PATH 里

第一版 `setup.bat` 只试 `python` 和 `py -3`，在本机直接失败 —— 这台机器上
只有 `D:\Anaconda3\python.exe`，`python` / `py` 都不在 PATH（`Get-Command python` 返回 not found）。

所以找 Python 的顺序扩成三段：

1. PATH 上的 `python`
2. Python 启动器 `py -3`
3. 一组常见安装位置（`%LOCALAPPDATA%\Programs\Python\Python313`、Anaconda/Miniconda 的
   用户目录与 `ProgramData`、根目录 `Python313`…）
4. 都找不到就**让用户输入路径**（支持把 `python.exe` 直接拖进窗口，脚本会去掉拖拽带来的引号）

实测第 3 段命中：`找到 Python 3.13.5   命令: "D:\Anaconda3\python.exe"`。

## 3. 编码：`.bat` 必须 GBK + CRLF

中文 Windows 的 cmd 用 OEM 代码页（936）逐行读取 bat 文件。写成 UTF-8 会显示成乱码，
而且一旦文件里混了 `chcp` 切换，cmd 按字节偏移续读还会进一步错位。

所以三个 bat 全部保存为 **GBK + CRLF**，并且加了 `.gitattributes`：

```
*.bat text eol=crlf
```

防止 Git 在 checkout 时把换行换成 LF（bat 对裸 LF 的容忍度很差）。

> 踩到的小坑：编辑工具往 GBK 的 bat 里插入中文时会写成 UTF-8 字节，
> 文件立刻变成混合编码、解码失败。所以后来统一改成"写完 → 用脚本转 GBK → 校验能解码"，
> 校验不过就不算完。

## 4. 验证方式（临时目录 + 桩程序）

不想每次测试都真的把 GUI 拉起来（会抢音频、写索引），所以搭了个临时目录：
复制一份 venv 的 `python.exe` / `pythonw.exe` + `pyvenv.cfg`（venv 的 exe 靠 `pyvenv.cfg`
里的 `home` 找标准库，复制这两个东西就能独立跑），再放一个 `run.py` 桩：

| 桩的行为 | 期望 | 实测 |
| --- | --- | --- |
| 睡 3 秒后正常退出 | `start.bat` 无任何输出 | ✅ 无输出，程序起来了 |
| 立刻 `sys.exit(1)` | 提示"没起来"+ 指路 debug.bat | ✅ 提示正确 |
| 目录里没有 `.venv` | 报"找不到虚拟环境"+ 指路 setup.bat | ✅ 提示正确 |
| `sys.exit(0)` | `debug.bat` 显示"退出码 0 / 正常退出" | ✅ |
| `sys.exit(3221225477)` | 显示非正常退出 + 退出码含义 | ✅ 但见下 |

最后一个用例暴露了一个细节：**Windows 下高位退出码在 cmd 里是负数**，
`0xC0000005` 显示成 `-1073741819` 而不是 `3221225477`。
所以 `debug.bat` 的提示列表按负数写，并加了一句说明，免得用户拿着
`-1073741819` 去搜却搜不到。

另外，PowerShell 捕获 bat 输出时会写成 UTF-16，直接看是乱码；
测试流程统一"重定向到文件 → 按 GBK/UTF-16 解码后再读"。

## 5. 打包：为什么现在不打包

用户说的是"等项目完善后打包"。理由确实成立：

- Phase 3（录屏）还没做完，现在打包出来的东西两周就过期
- 打包不是纯配置活，有两处代码要改（见 `docs/packaging.md` 第 5 节）：
  `ensure_mpv_dll()` 用 `__file__` 往上四层拼 `vendor/mpv`，冻结后这条路不存在，
  必须加 `sys._MEIPASS` 分支；OBS 路径解析同理
- 体积需要先治理：PySide6 全量 647 MB，其中 194 MB 是没人用的 QtWebEngine

方案要点（详见 `docs/packaging.md`）：

- **PyInstaller onedir**，不用 onefile：115 MB 的 dll 每次启动解压到 `%TEMP%` 太慢
- 先把 `PySide6` 换成 `PySide6-Essentials`（只 import 了 QtCore/Gui/Widgets）
- OBS（466 MB）做成可选，不进主包
- **许可证硬约束**：libmpv 是 GPLv2+、OBS 是 GPLv2、Qt 是 LGPLv3，
  分发二进制必须随包带许可证与源码获取说明
- 产出两个 exe：无控制台的日常版 + 带控制台的 debug 版（跟 bat 的两个入口对齐）

以上全部标记为**待验证**，执行时会逐条实测并把结果回填到 `docs/packaging.md`。
