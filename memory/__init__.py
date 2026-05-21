"""
记忆系统模块初始化

提供记忆系统和会话管理：
- SimpleMemory: 简单记忆系统（原有实现）
- DualMemory: 双层记忆系统（长期+短期）
- SessionManager: 会话管理器（本地化部署）
- ExperienceTree: 经验树索引管理
- TaskExperienceStore: 任务经验存储（树索引 + Markdown 文档）
"""

from memory.simple_memory import SimpleMemory
from memory.dual_memory import DualMemory
from memory.session_manager import SessionManager, SessionInfo
from memory.experience_tree import ExperienceTree, ExperienceNode, ExperienceLeaf, SummaryNode
from memory.task_experience import (
    TaskExperienceStore,
    TaskExperienceExtractor,
    TaskExperienceCapture,
    get_task_experience_store,
    get_task_experience_capture,
)

__all__ = [
    'SimpleMemory', 'DualMemory', 'SessionManager', 'SessionInfo',
    'ExperienceTree', 'ExperienceNode', 'ExperienceLeaf', 'SummaryNode',
    'TaskExperienceStore', 'TaskExperienceExtractor', 'TaskExperienceCapture',
    'get_task_experience_store', 'get_task_experience_capture',
]
