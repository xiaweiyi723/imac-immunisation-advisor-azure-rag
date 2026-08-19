#!/bin/bash
# 快速启动脚本 (macOS/Linux)
# Windows 用户请手动执行以下命令

# 1. 创建虚拟环境
echo "🔧 创建虚拟环境..."
python3 -m venv venv

# 2. 激活虚拟环境
echo "🔄 激活虚拟环境..."
source venv/bin/activate

# 3. 升级 pip
echo "📦 升级 pip..."
pip install --upgrade pip

# 4. 安装依赖
echo "📥 安装依赖..."
pip install -r requirements.txt

# 5. 运行测试脚本
echo ""
echo "🧪 运行配置测试..."
python test_config.py

echo ""
echo "✓ 设置完成！"
echo "现在可以运行: streamlit run app.py"
