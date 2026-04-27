"""
智能体模块初始化
"""

__all__ = ['FloodAgent']


def __getattr__(name):
    if name == 'FloodAgent':
        from agent.flood_agent import FloodAgent
        return FloodAgent
    raise AttributeError(name)
