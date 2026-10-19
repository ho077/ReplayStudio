@echo off
rem ============================================================
rem  ReplayStudio 调试启动（保留控制台窗口，退出后暂停）
rem  平时用 start.bat；程序闪退、白屏、报错时用这个。
rem ============================================================
setlocal
cd /d "%~dp0"
title ReplayStudio - 调试模式

set "APP=%~dp0run.py"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%APP%" goto no_app
if not exist "%PY%" goto no_venv

echo 解释器 : %PY%
echo 日志目录: %LOCALAPPDATA%\ReplayStudio\logs
echo 参数    : %*
echo ------------------------------------------------------------
echo.

"%PY%" "%APP%" %*
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
echo 退出码 : %RC%
if "%RC%"=="0" (
    echo 状态   : 正常退出
) else (
    echo 状态   : 非正常退出
    echo.
    echo 常见退出码：
    echo   1              Python 层未捕获异常（往上翻，找 traceback）
    echo   3              Python 初始化失败（虚拟环境损坏，重跑 setup.bat）
    echo   -1073741819    0xC0000005 访问冲突 / 段错误，多数来自播放引擎
    echo   -1073740791    0xC0000409 栈溢出 / 快速失败
    echo.
    echo Windows 下高位崩溃码会显示成负数：-1073741819 就是 0xC0000005。
    echo.
    echo 请把上面的输出，连同 %LOCALAPPDATA%\ReplayStudio\logs
    echo 下最新的 session-*.log / crash-*.log 一起保存反馈。
)
echo ------------------------------------------------------------
pause
exit /b %RC%

:no_app
echo [错误] 找不到 run.py：%APP%
echo        debug.bat 需要和 run.py 放在同一个目录。
echo.
pause
exit /b 1

:no_venv
echo [错误] 找不到虚拟环境：%~dp0.venv\Scripts\python.exe
echo        请先双击 setup.bat 准备环境。
echo.
pause
exit /b 1
