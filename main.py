"""
洪水预报智能体主程序

使用LangChain框架搭建的洪水预报智能体系统。
"""

import os
import logging
from dotenv import load_dotenv

# 必须在导入 settings 之前加载环境变量，否则 QwenConfig 初始化时读不到 .env 中的值
load_dotenv()

from config.settings import settings
from models import get_qwen_llm_service
from memory import SimpleMemory
from agent import FloodAgent


# 配置日志
logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(logs_dir, exist_ok=True)

# 创建日志格式
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# 配置根日志记录器
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# 移除已存在的处理器
if root_logger.handlers:
    for handler in root_logger.handlers:
        root_logger.removeHandler(handler)

# 创建文件处理器（按日期分割）
from logging.handlers import TimedRotatingFileHandler
file_handler = TimedRotatingFileHandler(
    os.path.join(logs_dir, 'floodagent.log'),
    when='midnight',
    interval=1,
    backupCount=30,
    encoding='utf-8'
)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)

# 创建控制台处理器
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)

# 添加处理器到根日志记录器
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)


def init_agent() -> FloodAgent:
    """
    初始化智能体
    
    Returns:
        FloodAgent实例
    """
    logger.info("=" * 60)
    logger.info("洪水预报智能体初始化")
    logger.info("=" * 60)
    
    # 1. 环境变量已在模块顶部加载
    logger.info("✓ 环境变量加载完成")
    
    # 2. 初始化Qwen大模型服务
    try:
        llm_service = get_qwen_llm_service(
            api_key=settings.qwen.api_key,
            model_name=settings.qwen.model_name,
            temperature=settings.qwen.temperature,
            max_tokens=settings.qwen.max_tokens,
        )
        logger.info(f"✓ 大模型服务初始化完成 - {settings.qwen.model_name}")
    except ValueError as e:
        logger.error(f"✗ 大模型服务初始化失败: {e}")
        logger.error("请设置环境变量: DASHSCOPE_API_KEY")
        raise
    
    # 3. 初始化记忆系统
    memory = SimpleMemory(
        max_history=settings.agent.max_history,
        context_window=settings.agent.context_window
    )
    logger.info(f"✓ 记忆系统初始化完成 - 最大历史: {settings.agent.max_history}轮")
    
    # 4. 创建智能体
    agent = FloodAgent(
        llm_service=llm_service,
        memory=memory,
        enable_chronos_warmup=settings.agent.enable_chronos_warmup
    )
    logger.info("✓ 智能体创建完成")
    
    logger.info("=" * 60)
    logger.info("初始化完成！")
    logger.info("=" * 60)
    
    return agent


def main():
    """主程序入口"""
    try:
        # 初始化智能体
        agent = init_agent()
        
        # 打印欢迎信息
        print("\n" + "=" * 60)
        print("洪水预报智能体")
        print("=" * 60)
        print("=" * 60 + "\n")
        
        # 交互式对话循环
        while True:
            try:
                # 获取用户输入
                user_input = input("\n用户: ").strip()
                
                # 处理特殊命令
                if user_input.lower() in ['exit', 'quit', 'q']:
                    print("\n再见！")
                    break
                
                if user_input.lower() == 'clear':
                    agent.clear_memory()
                    print("✓ 对话历史已清空")
                    continue
                
                if user_input.lower() == 'memory':
                    summary = agent.get_memory_summary()
                    print(f"\n记忆摘要:")
                    print(f"  最大历史: {summary['max_history']}轮")
                    print(f"  当前消息数: {summary['message_count']}")
                    continue
                
                if not user_input:
                    continue
                
                # 调用智能体处理用户输入（流式输出）
                print("\n助手: ", end="", flush=True)
                for chunk in agent.stream(user_input):
                    print(chunk, end="", flush=True)
                print()  # 换行
                
            except KeyboardInterrupt:
                print("\n\n检测到中断，退出中...")
                break
            except Exception as e:
                logger.error(f"处理用户输入时出错: {str(e)}")
                print(f"\n抱歉，出现错误: {str(e)}")
    
    except Exception as e:
        logger.error(f"程序启动失败: {str(e)}")
        print(f"\n错误: {str(e)}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
