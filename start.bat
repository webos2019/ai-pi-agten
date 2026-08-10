@echo off
chcp 65001 >nul
title Pi Agent - 智能助手平台

echo ============================================
echo   Pi Agent - 智能助手平台 启动脚本
echo ============================================
echo.

REM ── 进入项目目录 ──
cd /d "c:\newtask-pi"

REM ── 检查 .env 文件 ──
if not exist ".env" (
    echo [警告] 未找到 .env 文件，正在从 .env.example 创建...
    if exist ".env.example" (
        copy .env.example .env >nul
        echo [提示] 已创建 .env，请编辑填入 API Key 后重新运行。
        notepad .env
        pause
        exit /b 1
    ) else (
        echo [错误] .env 和 .env.example 均不存在，无法启动。
        pause
        exit /b 1
    )
)

echo [1/4] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
)
echo       Python OK

echo.
echo [2/4] 检查依赖包...
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo       依赖未安装，正在安装...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请手动运行: pip install -r requirements.txt
        pause
        exit /b 1
    )
) else (
    echo       依赖已安装
)

echo.
echo [3/4] 检查前端构建产物...
if exist "static\dist\index.html" (
    echo       前端已构建 (static\dist)
    set FRONTEND_MODE=production
) else (
    echo       前端未构建，检查 npm 环境...
    where npm >nul 2>&1
    if errorlevel 1 (
        echo [警告] 未找到 npm，将以纯后端模式启动（无前端界面）
        set FRONTEND_MODE=backend_only
    ) else (
        echo       发现 npm，以开发模式启动前后端...
        set FRONTEND_MODE=dev
    )
)

echo.
echo [4/4] 启动服务...
echo.

if "%FRONTEND_MODE%"=="production" (
    echo   模式: 生产模式 (后端托管前端静态文件)
    echo   地址: http://localhost:8000
    echo.
    echo   按 Ctrl+C 停止服务
    echo ============================================
    echo.
    python app.py
    pause
)

if "%FRONTEND_MODE%"=="dev" (
    echo   模式: 开发模式 (前后端分离热更新)
    echo   前端: http://localhost:3000
    echo   后端: http://localhost:8000
    echo.
    echo   正在启动后端 (新窗口)...
    start "Pi Agent Backend" cmd /k "cd /d c:\newtask-pi && python app.py"
    echo   正在启动前端 (新窗口)...
    timeout /t 2 /nobreak >nul
    start "Pi Agent Frontend" cmd /k "cd /d c:\newtask-pi\frontend && npm run dev"
    echo.
    echo ============================================
    echo   两个窗口已启动，请访问: http://localhost:3000
    echo   关闭对应窗口即可停止服务
    echo ============================================
    echo.
    echo 按任意键打开浏览器...
    pause >nul
    start http://localhost:3000
)

if "%FRONTEND_MODE%"=="backend_only" (
    echo   模式: 纯后端模式 (无前端界面)
    echo   地址: http://localhost:8000
    echo   API 文档: http://localhost:8000/docs
    echo.
    echo   按 Ctrl+C 停止服务
    echo ============================================
    echo.
    python app.py
    pause
)
