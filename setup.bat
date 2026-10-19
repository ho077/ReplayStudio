@echo off
rem ============================================================
rem  ReplayStudio 环境准备（只需跑一次）
rem  做三件事：找 Python -> 建 .venv -> 装依赖
rem  之后双击 start.bat 即可启动
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
title ReplayStudio - 环境准备

echo ============================================================
echo  ReplayStudio 环境准备
echo ============================================================
echo.

echo [1/3] 查找 Python（需要 3.10 或更高，推荐 3.13）...
call :find_python
if not defined BASEPY goto :ask_python

:have_py
%BASEPY% -c "import sys;print(sys.version.split()[0])" > "%TEMP%\rs_pyver.txt" 2>nul
set /p PYVER=<"%TEMP%\rs_pyver.txt"
del /q "%TEMP%\rs_pyver.txt" >nul 2>&1
if not defined PYVER set "PYVER=未知"
echo        找到 Python !PYVER!   命令: %BASEPY%
echo.

echo [2/3] 准备虚拟环境 .venv ...
if exist ".venv\Scripts\python.exe" (
    echo        已存在，跳过创建（要重建请先删掉 .venv 目录）。
) else (
    %BASEPY% -m venv .venv
    if errorlevel 1 goto :venv_fail
    echo        已创建 .venv
)
echo.

echo [3/3] 安装依赖 ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 (
    echo        直连 PyPI 失败，改用清华镜像重试 ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt ^
        -i https://pypi.tuna.tsinghua.edu.cn/simple --disable-pip-version-check
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败，请检查网络后重跑 setup.bat。
        echo.
        pause
        exit /b 1
    )
)
echo        依赖安装完成。
echo.

echo ------------------------------------------------------------
set "MISSING=0"
if exist "vendor\mpv\libmpv-2.dll" (
    echo [OK] 播放引擎 vendor\mpv\libmpv-2.dll
) else (
    set "MISSING=1"
    echo [缺] vendor\mpv\libmpv-2.dll —— 播放功能必需
    echo      下载 mpv-dev 的 x86_64 压缩包，解压出 libmpv-2.dll 放进 vendor\mpv\
    echo      来源: https://sourceforge.net/projects/mpv-player-windows/files/libmpv/
)
if exist "vendor\obs\obs64.exe" (
    echo [OK] 录屏后端 vendor\obs\obs64.exe
) else (
    echo [--] vendor\obs\obs64.exe 未就绪 —— 只影响录屏功能，不影响播放和媒体库
    echo      OBS Studio 便携版解压到 vendor\obs\ 即可
)
echo ------------------------------------------------------------
echo.
if "!MISSING!"=="0" (
    echo 环境就绪：双击 start.bat 启动（调试用 debug.bat）。
) else (
    echo 依赖装好了，但还差播放引擎（见上面 [缺] 那一行）。
    echo 补齐 libmpv-2.dll 之后再双击 start.bat。
)
echo.
pause
exit /b 0

rem ------------------------------------------------------------
rem 在 PATH、启动器和常见安装位置里找 Python
rem ------------------------------------------------------------
:find_python
set "BASEPY="
python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "BASEPY=python"
    goto :eof
)
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "BASEPY=py -3"
    goto :eof
)
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%USERPROFILE%\AppData\Local\Programs\Python\Python313\python.exe"
    "C:\Python313\python.exe"
    "C:\Program Files\Python313\python.exe"
    "%USERPROFILE%\Anaconda3\python.exe"
    "%USERPROFILE%\anaconda3\python.exe"
    "%USERPROFILE%\miniconda3\python.exe"
    "C:\ProgramData\Anaconda3\python.exe"
    "D:\Anaconda3\python.exe"
) do (
    if exist "%%~P" (
        "%%~P" -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
        if not errorlevel 1 (
            set BASEPY="%%~P"
            goto :eof
        )
    )
)
goto :eof

rem ------------------------------------------------------------
:ask_python
echo        PATH 里没找到 Python 3.10+，常见安装位置也没有。
echo.
echo        如果你知道 python.exe 在哪，把路径粘进来
echo        （也可以把 python.exe 直接拖进这个窗口），回车确认：
echo.
set /p "USERPY=python.exe 路径> "
if not defined USERPY goto :give_up
set "USERPY=%USERPY:"=%"
if not exist "%USERPY%" (
    echo        路径不存在：%USERPY%
    echo.
    goto :ask_python
)
"%USERPY%" -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo        版本太低（需要 3.10 以上）：%USERPY%
    echo.
    goto :ask_python
)
set BASEPY="%USERPY%"
goto :have_py

:venv_fail
echo.
echo [错误] 创建虚拟环境失败。
echo        多半是目录不可写：请把项目放到可写目录（不要放 C:\Program Files）。
echo.
pause
exit /b 1

:give_up
echo.
echo [放弃] 没有 Python 就没法继续。
echo       到 https://www.python.org/downloads/windows/ 下载 3.13 安装包，
echo       安装时勾选 "Add python.exe to PATH"，装完再双击 setup.bat。
echo.
pause
exit /b 1
