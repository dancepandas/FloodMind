"""
记忆系统模块初始化

提供记忆系统和会话管理：
- DualMemory: 唯一记忆系统（扁平 _turns 历史源 + 精简上下文 + 持久化）
- SessionManager: 会话管理器（本地化部署）
- ExperienceTree: 经验树索引管理
- TaskExperienceStore: 任务经验存储（树索引 + Markdown 文档）
"""

from floodmind.memory.dual_memory import DualMemory
from floodmind.memory.session_manager import SessionManager, SessionInfo
from floodmind.memory.experience_tree import ExperienceTree, ExperienceNode, ExperienceLeaf, SummaryNode
from floodmind.memory.task_experience import (
    TaskExperienceStore,
    TaskExperienceExtractor,
    TaskExperienceCapture,
    get_task_experience_store,
    get_task_experience_capture,
)

__all__ = [
    'DualMemory', 'SessionManager', 'SessionInfo',
    'ExperienceTree', 'ExperienceNode', 'ExperienceLeaf', 'SummaryNode',
    'TaskExperienceStore', 'TaskExperienceExtractor', 'TaskExperienceCapture',
    'get_task_experience_store', 'get_task_experience_capture',
]
