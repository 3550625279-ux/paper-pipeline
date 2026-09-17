@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem  PaperPipeline control
rem    pipeline.cmd            看状态
rem    pipeline.cmd start      启动服务
rem    pipeline.cmd stop       停止服务
rem    pipeline.cmd restart    重启
rem    pipeline.cmd logs       看最近日志

set "PPY="
set "PPW="
set "PORT="

if exist "%~dp0env.cmd" call "%~dp0env.cmd" >nul 2>&1

if defined PP_PYTHON if exist "%PP_PYTHON%" set "PPY=%PP_PYTHON%"
if defined PP_PYTHONW if exist "%PP_PYTHONW%" set "PPW=%PP_PYTHONW%"
if defined PP_PORT set "PORT=%PP_PORT%"

if not defined PPY (
    where py >nul 2>&1 && set "PPY=py -3"
)
if not defined PPY (
    where python >nul 2>&1 && set "PPY=python"
)
if not defined PPW set "PPW=%PPY%"
if not defined PORT set "PORT=8787"

if not defined PPY (
    echo   找不到 Python。请先运行 install.cmd。
    pause
    exit /b 1
)

set "CMD=%~1"
if "%CMD%"=="" set "CMD=status"

if /i "%CMD%"=="start"   goto :start
if /i "%CMD%"=="stop"    goto :stop
if /i "%CMD%"=="restart" goto :restart
if /i "%CMD%"=="logs"    goto :logs
if /i "%CMD%"=="status"  goto :status
echo   用法: pipeline.cmd [start^|stop^|restart^|status^|logs]
exit /b 2

:start
echo   正在启动服务...
start "" "%PPW%" "%~dp0run_service.pyw"
timeout /t 3 >nul
goto :status

:stop
"%PPY%" "%~dp0manage.py" stop
exit /b 0

:restart
"%PPY%" "%~dp0manage.py" stop
timeout /t 2 >nul
goto :start

:status
"%PPY%" "%~dp0manage.py" status
exit /b 0

:logs
"%PPY%" "%~dp0manage.py" logs
exit /b 0
