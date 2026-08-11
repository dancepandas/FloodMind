"""子代理权限上下文（目标 §13.2/§2.7）。

子代理获得独立的 PathService（绑定 child workspace，folder-first）与
PermissionService（父规则快照，无 AskService，tier 硬门）——不共享父的可变
rule list 与 ask 出口。WIP contracts/permissions.py 不改，仅导入其契约。
"""
from pathlib import Path

from floodmind.agent.runtime.contracts.workspace import Workspace
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.permission_service import PermissionService


def build_child_permission_context(
    *,
    parent_path_service: PathService,
    parent_permission_service: PermissionService,
    child_workspace,
    child_session_id: str,
):
    """构造子代理专用的 (PermissionService, PathService)。

    - child PathService：project_root 同父；workspace = child-workspace（folder-first），
      extra_read_roots 继承父（skill 只读根等）。
    - child PermissionService：ask_service=None（子代理无权 ASK）；父 deny/allow 规则
      复制为不可变快照；tier 硬门（agent_tier=="sub"）继续生效。
    """
    child_root = Path(child_workspace)
    child_ws = Workspace.from_folder(child_root, session_id=child_session_id)
    child_path = PathService(
        project_root=parent_path_service._project_root,
        workspace=child_ws,
        extra_read_roots=tuple(parent_path_service._extra_read_roots),
    )
    child_perm = PermissionService(ask_service=None, path_service=child_path)
    for rule in parent_permission_service._deny_rules:
        child_perm.add_deny_rule(rule)
    for rule in parent_permission_service._allow_rules:
        child_perm.add_allow_rule(rule)
    return child_perm, child_path
