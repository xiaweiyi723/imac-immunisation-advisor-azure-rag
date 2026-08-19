"""
配置测试脚本
用于验证 Azure 连接和 Agent 配置是否正确
"""

import os
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
import sys

def test_environment_variables():
    """检查环境变量"""
    print("=" * 60)
    print("📋 检查环境变量...")
    print("=" * 60)
    
    load_dotenv()
    
    project_endpoint = os.getenv("PROJECT_ENDPOINT")
    agent_name = os.getenv("AGENT_NAME")
    agent_version = os.getenv("AGENT_VERSION")
    
    errors = []
    
    if not project_endpoint:
        errors.append("❌ PROJECT_ENDPOINT 未设置")
    else:
        print(f"✓ PROJECT_ENDPOINT: {project_endpoint}")
    
    if not agent_name:
        errors.append("❌ AGENT_NAME 未设置")
    else:
        print(f"✓ AGENT_NAME: {agent_name}")
    
    if not agent_version:
        errors.append("❌ AGENT_VERSION 未设置")
    else:
        print(f"✓ AGENT_VERSION: {agent_version}")
    
    if errors:
        for error in errors:
            print(error)
        return False
    
    return True


def test_azure_authentication():
    """测试 Azure 认证"""
    print("\n" + "=" * 60)
    print("🔐 测试 Azure 认证...")
    print("=" * 60)
    
    try:
        credential = DefaultAzureCredential()
        # 尝试获取令牌来验证认证
        token = credential.get_token("https://management.azure.com/.default")
        print("✓ Azure 认证成功")
        print(f"✓ 令牌已获取 (有效期至: ...)")
        return True
    except Exception as e:
        print(f"❌ Azure 认证失败: {str(e)}")
        print("\n💡 解决方法:")
        print("   1. 运行: az login")
        print("   2. 确保使用正确的 Azure 账户")
        print("   3. 尝试: az account show")
        return False


def test_azure_client():
    """测试 Azure AI Project Client"""
    print("\n" + "=" * 60)
    print("🤖 测试 Azure AI Project Client...")
    print("=" * 60)
    
    load_dotenv()
    
    project_endpoint = os.getenv("PROJECT_ENDPOINT")
    
    try:
        credential = DefaultAzureCredential()
        client = AIProjectClient(
            endpoint=project_endpoint,
            credential=credential
        )
        print("✓ Azure AI Project Client 连接成功")
        
        # 尝试获取 OpenAI 客户端
        openai_client = client.get_openai_client()
        print("✓ OpenAI 客户端已获取")
        
        return True
    except Exception as e:
        print(f"❌ Azure AI Project Client 连接失败: {str(e)}")
        print("\n💡 解决方法:")
        print("   1. 检查 PROJECT_ENDPOINT 是否正确")
        print("   2. 检查网络连接")
        print("   3. 检查 Azure 凭证权限")
        return False


def test_agent_call():
    """测试调用 Agent"""
    print("\n" + "=" * 60)
    print("📞 测试调用 Agent...")
    print("=" * 60)
    
    load_dotenv()
    
    project_endpoint = os.getenv("PROJECT_ENDPOINT")
    agent_name = os.getenv("AGENT_NAME")
    agent_version = os.getenv("AGENT_VERSION")
    
    try:
        credential = DefaultAzureCredential()
        project_client = AIProjectClient(
            endpoint=project_endpoint,
            credential=credential
        )
        openai_client = project_client.get_openai_client()
        
        print(f"🔄 正在调用 Agent: {agent_name} (v{agent_version})")
        print("   问题: 'Tell me what you can help with.'")
        
        response = openai_client.responses.create(
            input=[{"role": "user", "content": "Tell me what you can help with."}],
            extra_body={
                "agent_reference": {
                    "name": agent_name,
                    "version": agent_version,
                    "type": "agent_reference"
                }
            },
        )
        
        print("✓ Agent 调用成功!")
        print(f"\n📝 Agent 回复:")
        print("-" * 60)
        
        response_text = response.output_text if hasattr(response, "output_text") else str(response)
        
        # 显示回复（最多前 500 个字符）
        if len(response_text) > 500:
            print(response_text[:500] + "...")
        else:
            print(response_text)
        
        print("-" * 60)
        return True
        
    except Exception as e:
        print(f"❌ Agent 调用失败: {str(e)}")
        print("\n💡 解决方法:")
        print("   1. 检查 AGENT_NAME 是否正确")
        print("   2. 检查 AGENT_VERSION 是否正确")
        print("   3. 检查 Agent 是否已发布")
        print("   4. 检查 Agent 是否有 File Search 启用")
        return False


def main():
    """主函数"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "  🏥 医学诊疗智能助手 - 配置测试".center(58) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "=" * 58 + "╝")
    
    results = {
        "环境变量": test_environment_variables(),
        "Azure 认证": test_azure_authentication(),
        "AI Project Client": test_azure_client(),
        "Agent 调用": test_agent_call()
    }
    
    print("\n" + "=" * 60)
    print("📊 测试结果总结")
    print("=" * 60)
    
    all_passed = True
    for test_name, result in results.items():
        status = "✓ 通过" if result else "❌ 失败"
        print(f"{test_name:20} {status}")
        if not result:
            all_passed = False
    
    print("=" * 60)
    
    if all_passed:
        print("\n✓ 所有测试通过！")
        print("现在可以运行应用: streamlit run app.py\n")
        return 0
    else:
        print("\n❌ 有些测试失败，请按照上面的提示进行修复。\n")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n❌ 发生异常: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
