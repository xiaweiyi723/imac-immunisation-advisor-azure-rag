#!/usr/bin/env python3
"""
一键启动脚本 - 自动设置和运行应用
"""

import os
import sys
import subprocess
import platform

def run_command(cmd, description=""):
    """执行命令"""
    if description:
        print(f"\n🔄 {description}...")
    print(f"   $ {cmd}")
    result = os.system(cmd)
    return result == 0

def main():
    print("\n" + "=" * 70)
    print("  🏥 医学诊疗智能助手 - 一键启动".center(70))
    print("=" * 70)
    
    # 检查 Python 版本
    version = sys.version_info
    if version.major < 3 or version.minor < 8:
        print(f"\n❌ Python 版本过低: {version.major}.{version.minor}")
        print("   需要 Python 3.8 或更高版本")
        return 1
    
    print(f"\n✓ Python 版本: {version.major}.{version.minor}.{version.micro}")
    
    # 检查操作系统
    system = platform.system()
    print(f"✓ 操作系统: {system}")
    
    # 检查 Azure CLI
    print("\n🔍 检查 Azure CLI...")
    result = os.system("az --version > nul 2>&1" if system == "Windows" else "az --version > /dev/null 2>&1")
    if result != 0:
        print("❌ Azure CLI 未安装或不在 PATH 中")
        print("   请先安装: https://docs.microsoft.com/zh-cn/cli/azure/install-azure-cli")
        return 1
    print("✓ Azure CLI 已安装")
    
    # 创建虚拟环境
    venv_dir = "venv"
    if not os.path.exists(venv_dir):
        if not run_command(f"python -m venv {venv_dir}", "创建虚拟环境"):
            print("❌ 创建虚拟环境失败")
            return 1
    else:
        print(f"\n✓ 虚拟环境已存在: {venv_dir}")
    
    # 确定激活脚本
    if system == "Windows":
        activate_script = os.path.join(venv_dir, "Scripts", "activate.bat")
        pip_cmd = os.path.join(venv_dir, "Scripts", "pip")
        python_cmd = os.path.join(venv_dir, "Scripts", "python")
    else:
        activate_script = os.path.join(venv_dir, "bin", "activate")
        pip_cmd = os.path.join(venv_dir, "bin", "pip")
        python_cmd = os.path.join(venv_dir, "bin", "python")
    
    # 安装/升级 pip
    print("\n🔄 升级 pip...")
    if system == "Windows":
        os.system(f"{python_cmd} -m pip install --upgrade pip > nul 2>&1")
    else:
        os.system(f"{python_cmd} -m pip install --upgrade pip > /dev/null 2>&1")
    
    # 安装依赖
    if not run_command(f"{pip_cmd} install -r requirements.txt", "安装依赖"):
        print("❌ 安装依赖失败")
        return 1
    
    # 检查 .env 文件
    print("\n🔍 检查配置文件...")
    if not os.path.exists(".env"):
        if os.path.exists(".env.example"):
            print("⚠️  .env 文件不存在，从 .env.example 复制...")
            if system == "Windows":
                os.system("copy .env.example .env")
            else:
                os.system("cp .env.example .env")
            print("ℹ️  请编辑 .env 文件，填入 AGENT_NAME 和 AGENT_VERSION")
        else:
            print("❌ .env.example 文件不存在")
            return 1
    else:
        print("✓ .env 文件已存在")
    
    # 运行测试脚本
    print("\n" + "=" * 70)
    print("  🧪 运行配置测试".center(70))
    print("=" * 70)
    
    if system == "Windows":
        test_result = os.system(f"{python_cmd} test_config.py")
    else:
        test_result = os.system(f"{python_cmd} test_config.py")
    
    if test_result != 0:
        print("\n⚠️  配置测试失败或有警告")
        print("请根据上面的提示进行修复")
        input("按 Enter 继续或 Ctrl+C 退出...")
    
    # 启动应用
    print("\n" + "=" * 70)
    print("  🚀 启动 Streamlit 应用".center(70))
    print("=" * 70)
    print("\n应用将在浏览器中打开（通常是 http://localhost:8501）")
    print("按 Ctrl+C 停止应用\n")
    
    # 构建启动命令
    if system == "Windows":
        streamlit_cmd = f"{python_cmd} -m streamlit run app.py"
    else:
        streamlit_cmd = f"{python_cmd} -m streamlit run app.py"
    
    result = os.system(streamlit_cmd)
    
    return 0 if result == 0 else 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n👋 已退出")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
