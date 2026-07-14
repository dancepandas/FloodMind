"""
Runtime Contracts — 路径协议模型

路径解析的输入/输出集中定义，不依赖业务实现。
PathService 只依赖此模块。
"""

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel


class PathResolveRequest(BaseModel):
    session_id: str = ""
    raw_path: str = ""
    access: Literal["read", "write", "exec", "cwd"] = "read"


class PathResolveResult(BaseModel):
    raw_path: str = ""
    normalized_path: str = ""
    resolved_path: str = ""
    source: Literal["absolute", "user_dir", "upload_dir", "project_root_fallback", "no_context_rejected"] = "project_root_fallback"
    allowed: bool = True
    reason: str = ""

    @property
    def resolved(self) -> Path:
        return Path(self.resolved_path)
