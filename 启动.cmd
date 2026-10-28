@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title POSSIM-Web 珠江口风暴潮淹没推演平台

echo ============================================================
echo   POSSIM-Web . 珠江口风暴潮淹没推演平台
echo ============================================================
echo   当前目录 : %CD%
echo   访问地址 : http://127.0.0.1:8848/
echo   打开方式 : 浏览器访问上面的 http 地址（不要双击 index.html）
echo ------------------------------------------------------------

rem ---- 检查 python 是否在 PATH 上 ----
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未在 PATH 中找到 python 命令。
    echo        请安装 Python 3.10+ 并在安装时勾选 "Add Python to PATH"，
    echo        或先用完整路径启动：python server.py --port 8848
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%v in ('python -c "import sys;print(sys.version.split()[0])" 2^>nul') do set "PYVER=%%v"
echo [检查] python 版本 : !PYVER!
if not defined PYVER (
    echo [错误] python 无法运行，请检查安装。
    pause
    exit /b 1
)

rem ---- 1 秒后自动打开浏览器（后台隐藏窗口执行，不阻塞服务）----
rem     server 起来后浏览器才请求，避免 "无法访问" 一次空白
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 1; Start-Process 'http://127.0.0.1:8848/'"

echo [启动] python server.py --port 8848
echo ------------------------------------------------------------
python server.py --port 8848
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo [提示] 服务退出码 %EXITCODE%。
    echo        如果提示端口被占用，请改端口启动，例如：
    echo        python server.py --port 8849
    echo        然后浏览器打开 http://127.0.0.1:8849/
) else (
    echo [提示] 服务已正常停止。
)
echo.
pause
endlocal
exit /b %EXITCODE%
