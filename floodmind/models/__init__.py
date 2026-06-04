"""
模型模块

统一的 LLM 服务入口：floodmind.agent.native.model_client.ModelClient
所有调用方均通过 ModelClient.from_settings() 或 ModelClient.from_settings_with_preset() 构造实例。
"""

from floodmind.agent.native.model_client import ModelClient

__all__ = ['ModelClient']
