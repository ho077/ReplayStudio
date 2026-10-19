@echo off
rem ============================================================
rem  ReplayStudio 启动器（静默启动，不保留控制台窗口）
rem  首次使用：先双击 setup.bat 准备环境
rem  启动失败 / 想看报错：双击 debug.bat
rem ============================================================
setlocal
cd /d "%~dp0"

set "APP=%~dp0run.py"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "DLL=%~dp0vendor\mpv\libmpv-2.dll"
set "BEFORE=%TEMP%\rs_procs_before.txt"
set "AFTER=%TEMP%\rs_procs_after.txt"

if not exist "%APP%" goto no_app
if not exist "%PYW%" goto no_venv

if not exist "%DLL%" (
    echo [警告] 缺少播放引擎 vendor\mpv\libmpv-2.dll
    echo        程序能打开，但点播放会提示“播放引擎不可用”。
    echo        下载 mpv-dev 的 x86_64 包，把 libmpv-2.dll 放进 vendor\mpv\ 即可。
    echo.
)

rem 先记下现有的 pythonw 进程：只按名字找会把别人残留的进程也算进去，
rem 所以要前后对比一次，才能判断新进程到底起没起来。
tasklist /fi "imagename eq pythonw.exe" /nh > "%BEFORE%" 2>nul

rem %* 把命令行参数（例如“打开方式”传来的视频路径）一并转交给程序
start "" "%PYW%" "%APP%" %*

rem pythonw 是窗口程序，start 立刻返回；等一秒再看有没有新进程
>nul 2>&1 ping -n 2 127.0.0.1
tasklist /fi "imagename eq pythonw.exe" /nh > "%AFTER%" 2>nul
fc "%BEFORE%" "%AFTER%" >nul 2>&1
rem del 会把 ERRORLEVEL 清零，所以先把对比结果存下来再清理临时文件
set "SAME=%ERRORLEVEL%"
del /q "%BEFORE%" "%AFTER%" >nul 2>&1
if "%SAME%"=="0" (
    echo [提示] 程序好像没起来（启动瞬间就退出了）。
    echo        请双击 debug.bat，那里会显示完整的错误信息。
    echo.
    pause
)
exit /b 0

:no_app
echo [错误] 找不到 run.py：%APP%
echo        请确认 start.bat 与 run.py 放在同一个目录下。
echo.
pause
exit /b 1

:no_venv
echo [错误] 找不到虚拟环境：%~dp0.venv\Scripts\pythonw.exe
echo.
echo        如果是刚克隆下来的项目，请先双击 setup.bat，
echo        它会创建 .venv 并安装 PySide6 / python-mpv / cryptography。
echo.
pause
exit /b 1
