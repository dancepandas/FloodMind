"""
Agent Runtime — 统一入口

contracts: 协议模型（纯数据结构，无业务逻辑）
services: 可复用服务（PathService, AskService, PermissionService, ToolExecutionService）
adapters: 适配层（Flask API, SSE, Native/LangChain 工具适配）
"""