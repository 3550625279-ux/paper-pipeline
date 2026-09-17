@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo  PaperPipeline - 安装向导
echo  ====================================
echo.

set "PPY="

rem 1) reuse a previously configured interpreter
if exist "%~dp0env.cmd" (
    call "%~dp0env.cmd" >nul 2>&1
    if defined PP_PYTHON if exist "%PP_PYTHON%" set "PPY=%PP_PYTHON%"
)

rem 2) the Windows py launcher
if not defined PPY (
    where py >nul 2>&1 && set "PPY=py -3"
)

rem 3) plain python on PATH
if not defined PPY (
    where python >nul 2>&1 && set "PPY=python"
)

if not defined PPY (
    echo   没有找到 Python。
    echo.
    echo   请先安装 Python 3.9 或更新版本：https://www.python.org/downloads/
    echo   安装时记得勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

%PPY% "%~dp0setup.py"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo   安装结束。按任意键关闭。
) else (
    echo   安装没有正常结束（代码 %RC%）。按任意键关闭。
)
pause >nul
exit /b %RC%
