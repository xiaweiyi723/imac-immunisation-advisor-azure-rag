@echo off
REM 快速启动脚本 (Windows)

echo.
echo ======================================
echo   医学诊疗智能助手 - 快速启动
echo ======================================
echo.

REM 1. 创建虚拟环境
echo [1/5] 创建虚拟环境...
python -m venv venv
if errorlevel 1 (
    echo ❌ 创建虚拟环境失败
    pause
    exit /b 1
)

REM 2. 激活虚拟环境
echo [2/5] 激活虚拟环境...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo ❌ 激活虚拟环境失败
    pause
    exit /b 1
)

REM 3. 升级 pip
echo [3/5] 升级 pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo ⚠️  pip 升级失败，继续...
)

REM 4. 安装依赖
echo [4/5] 安装依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo ❌ 安装依赖失败
    pause
    exit /b 1
)

REM 5. 运行测试脚本
echo [5/5] 运行配置测试...
python test_config.py

echo.
echo ======================================
echo   ✓ 设置完成！
echo ======================================
echo.
echo 现在可以运行以下命令启动应用:
echo.
echo   streamlit run app.py
echo.
pause
