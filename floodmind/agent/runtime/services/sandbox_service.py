"""
SandboxService — 子代理执行隔离。

为每个子代理创建独立工作区，控制其可写范围，并在子代理结束后
清理进程树、回流产物。
"""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from floodmind.agent.runtime.services.process_sandbox import (
    ProcessSandbox,
    register_process_sandbox,
    unregister_process_sandbox,
)
from floodmind.agent.runtime.services.workspace_service import get_workspace

logger = logging.getLogger(__name__)


class SandboxContext(BaseModel):
    """子代理沙盒上下文。"""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    sub_session_id: str
    workspace_dir: Path
    outputs_dir: Path
    uploads_dir: Path
    parent_output_dir: Optional[Path] = None
    # 阶段C：主代理委派时指定的工作目录（桌面版并行写 user_dir 子目录）。
    # 子代理默认 cwd 优先用它，其次 workspace_dir。None=未指定，走 sandbox。
    delegate_cwd: Optional[Path] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SandboxService:
    """管理子代理工作区与产物回流。"""

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        keep_workspace: bool = False,
        workspace: Optional["Workspace"] = None,
    ):
        if base_dir:
            self._base_dir = Path(base_dir)
        else:
            # 优先 workspace.sandbox_base；回退 PROJECT_ROOT/data/sessions（兼容测试/旧布局）
            ws = workspace or self._resolve_workspace()
            if ws is not None:
                self._base_dir = Path(ws.sandbox_base)
            else:
                from floodmind.agent.runtime.services._runtime_root import PROJECT_ROOT
                self._base_dir = PROJECT_ROOT / "data" / "sessions"
        self._keep_workspace = keep_workspace
        self._workspace = workspace

    @staticmethod
    def _resolve_workspace() -> Optional["Workspace"]:
        return get_workspace()

    # ── public API ──────────────────────────────────────────────────────────

    def create(
        self,
        sub_session_id: str,
        parent_output_dir: Optional[Path] = None,
        delegate_cwd: Optional[Path] = None,
    ) -> SandboxContext:
        """为子代理创建一个独立工作区。

        delegate_cwd（阶段C）：主代理委派时指定子代理工作目录。
        - 指定且存在：子代理默认 cwd = delegate_cwd（桌面版直接在 user_dir/子目录干活，无需回流）。
        - 未指定：子代理默认 cwd = workspace_dir（网页版旧 sandbox 隔离行为）。
        """
        session_dir = self._base_dir / sub_session_id
        workspace_dir = session_dir / "workspace"
        outputs_dir = workspace_dir / "outputs"
        uploads_dir = workspace_dir / "uploads"

        workspace_dir.mkdir(parents=True, exist_ok=True)
        outputs_dir.mkdir(parents=True, exist_ok=True)
        uploads_dir.mkdir(parents=True, exist_ok=True)

        # delegate_cwd 落盘校验：指定时需存在且是目录
        effective_delegate = None
        if delegate_cwd is not None:
            dp = Path(delegate_cwd)
            try:
                dp.mkdir(parents=True, exist_ok=True)
                if dp.is_dir():
                    effective_delegate = dp
            except Exception:
                logger.warning("SandboxService: delegate_cwd %s 无效，回退 sandbox", delegate_cwd)

        process_sandbox = ProcessSandbox(workspace_dir=workspace_dir)
        register_process_sandbox(sub_session_id, process_sandbox)

        ctx = SandboxContext(
            sub_session_id=sub_session_id,
            workspace_dir=workspace_dir,
            outputs_dir=outputs_dir,
            uploads_dir=uploads_dir,
            parent_output_dir=Path(parent_output_dir) if parent_output_dir else None,
            delegate_cwd=effective_delegate,
        )
        logger.info(
            "SandboxService: created workspace for %s at %s (delegate_cwd=%s)",
            sub_session_id,
            workspace_dir,
            effective_delegate,
        )
        return ctx

    def copy_artifacts_to_parent(
        self,
        ctx: SandboxContext,
        artifact_paths: List[str],
    ) -> List[str]:
        """将 workspace 中的产物复制到父 output_dir，并返回父目录路径列表。"""
        if not ctx.parent_output_dir:
            return artifact_paths

        parent_dir = ctx.parent_output_dir
        parent_dir.mkdir(parents=True, exist_ok=True)
        copied: List[str] = []

        parent_dir_resolved = parent_dir.resolve()

        for src_str in artifact_paths:
            src = Path(src_str)
            if not src.exists():
                copied.append(src_str)
                continue
            # Prefer relative to outputs_dir; fall back to workspace_dir
            try:
                rel = src.relative_to(ctx.outputs_dir)
            except ValueError:
                try:
                    rel = src.relative_to(ctx.workspace_dir)
                except ValueError:
                    rel = Path(src.name)
            dst = parent_dir / rel

            # 路径遍历防护：目标必须落在 parent_dir 内
            try:
                dst.resolve().relative_to(parent_dir_resolved)
            except ValueError:
                logger.warning(
                    "SandboxService: copy artifact %s skipped (path traversal outside parent_dir)",
                    src,
                )
                copied.append(src_str)
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(src), str(dst))
                copied.append(str(dst))
            except Exception as e:
                logger.warning("SandboxService: copy artifact %s failed: %s", src, e)
                copied.append(src_str)

        return copied

    def destroy(self, ctx: SandboxContext) -> None:
        """销毁沙盒。默认删除 workspace（除非 keep_workspace=True）。"""
        process_sandbox = unregister_process_sandbox(ctx.sub_session_id)
        if process_sandbox is not None:
            try:
                process_sandbox.terminate_all()
            except Exception as e:
                logger.warning("SandboxService: terminate process sandbox failed: %s", e)

        if self._keep_workspace:
            logger.info("SandboxService: keeping workspace %s", ctx.workspace_dir)
            return
        try:
            shutil.rmtree(ctx.workspace_dir, ignore_errors=True)
            logger.info("SandboxService: destroyed workspace %s", ctx.workspace_dir)
        except Exception as e:
            logger.warning("SandboxService: failed to destroy workspace %s: %s", ctx.workspace_dir, e)

    # ── helpers ─────────────────────────────────────────────────────────────

    def workspace_for(self, sub_session_id: str) -> Path:
        return self._base_dir / sub_session_id / "workspace"
