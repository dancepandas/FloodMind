"""Sandbox 契约（target §11.4）。"""

from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field


class ResourceLimits(BaseModel):
    """沙盒资源上限。"""

    max_seconds: float = 120.0          # 墙钟时间上限（秒）
    max_cpu_seconds: Optional[float] = None
    max_memory_mb: Optional[int] = None
    max_output_bytes: int = 10 * 1024 * 1024  # stdout+stderr 累计上限
    max_processes: int = 32


class SandboxPolicy(BaseModel):
    """沙盒策略（§11.4）。file_root 为强制文件根。"""

    label: str = "local-restricted"
    file_root: str                       # 文件根：cwd/temp 必须落在其内
    allow_network: bool = False
    env_allowlist: List[str] = Field(default_factory=list)  # 允许透传的父环境变量
    secret_inject: Dict[str, str] = Field(default_factory=dict)  # 注入的 Secret
    resources: ResourceLimits = Field(default_factory=ResourceLimits)


class ToolInvocation(BaseModel):
    """一次待执行调用（§11.4）。command 为 argv（shell=False）。"""

    command: List[str]
    cwd: str
    env: Dict[str, str] = Field(default_factory=dict)
    stdin_bytes: Optional[bytes] = None
    timeout_seconds: Optional[float] = None  # 覆盖 policy.resources.max_seconds


class ExecutionResult(BaseModel):
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    timed_out: bool = False
    cancelled: bool = False
    sandbox_violation: Optional[str] = None
    pid: Optional[int] = None


class SandboxViolation(Exception):
    """沙盒强制边界被违反。"""


# 取消令牌：返回 True 表示请求取消
CancellationToken = Callable[[], bool]
