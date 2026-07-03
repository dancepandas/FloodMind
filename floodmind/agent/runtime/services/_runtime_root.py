"""
单一来源的 PROJECT_ROOT 常量。

历史问题：`_PROJECT_ROOT = Path.cwd()` 曾在 6 个模块各自硬编码，导致：
1. 从不同 cwd 启动（如桌面版从用户文件夹启动）时 project_root 错位；
2. 模块级常量 import 时固化，运行时无法统一注入。

本模块只依赖标准库，无任何 floodmind 反向依赖，可被任意模块安全 import。
所有需要"项目根"的模块统一从这里取，消除重复定义。

解析优先级：
1. 环境变量 FLOODMIND_PROJECT_ROOT（显式指定，桌面版/测试用）
2. floodmind 包父目录（包安装在 <repo>/floodmind 时即仓库根）
"""

import os
from pathlib import Path

_ENV_VAR = "FLOODMIND_PROJECT_ROOT"


def _resolve_project_root() -> Path:
    env_root = os.environ.get(_ENV_VAR, "").strip()
    if env_root:
        p = Path(env_root).resolve()
        return p

    # 本文件位于 floodmind/agent/runtime/services/_runtime_root.py
    # services → runtime → agent → floodmind（3 级），再 .parent 即仓库根。
    pkg_dir = Path(__file__).resolve().parent  # .../floodmind/agent/runtime/services
    for _ in range(3):
        pkg_dir = pkg_dir.parent
    # pkg_dir 现在应为 floodmind 包目录
    repo_root = pkg_dir.parent
    return repo_root.resolve()


PROJECT_ROOT: Path = _resolve_project_root()