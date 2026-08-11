"""
PermissionService — 统一权限检查服务

修复旧 PermissionManager 的核心问题：
1. 全局 allow 规则不能覆盖工具级 ASK
2. 所有工具必须显式声明权限策略
3. ASK、DENY、ALLOW 逻辑集中，不散落在工具和 executor 里

优先级顺序（固定，不可覆盖）：
1. 工具级 policy 检查 → 如果 DENY，直接 DENY
2. 工具级 policy 检查 → 如果 ASK，进入 AskService，不允许全局 allow 覆盖
3. 全局 deny rules
4. 全局 allow rules（只在工具级结果是 ALLOW 时生效）
5. 默认：按工具级 policy 的结果返回

设计原则：
- ToolSpec.permission_policy 为 None 时默认 DENY（fail closed）
- AskService 是唯一 ASK 出口
- PermissionService 不直接持有 ASK callback
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior,
    PermissionDecision,
    PermissionRequest,
    PermissionRule,
    ToolFeedback,
    ToolPermissionPolicy,
)
from floodmind.agent.runtime.contracts.paths import PathResolveResult
from floodmind.agent.runtime.services.exec_write_scanner import (
    approve_unresolved_exec_writes,
    check_exec_write_targets,
    dangerous_command_reason,
    scan_exec_writes,
)

logger = logging.getLogger(__name__)


class PermissionService:
    def __init__(self, ask_service=None, path_service=None):
        self._deny_rules: List[PermissionRule] = []
        self._allow_rules: List[PermissionRule] = []
        self._ask_service = ask_service
        self._path_service = path_service
        self._mutating_command_patterns = [
            re.compile(r'(^|[^>])>{1,2}(?!\s*&)', re.IGNORECASE),
            re.compile(r'\b(rm|del|rmdir|rd)\b', re.IGNORECASE),
            re.compile(r'\b(mv|move)\b', re.IGNORECASE),
            re.compile(r'\b(cp|copy|xcopy|robocopy)\b', re.IGNORECASE),
            re.compile(r'\b(Set-Content|Out-File|Move-Item|Copy-Item|Remove-Item)\b', re.IGNORECASE),
        ]
        self._network_or_credential_command_patterns = [
            re.compile(r'\b(curl|wget|Invoke-WebRequest|iwr)\b', re.IGNORECASE),
            re.compile(r'\b(KEY|TOKEN|SECRET|PASSWORD|API_KEY)\b', re.IGNORECASE),
        ]
        self._content_threat_patterns: List[tuple] = [
            ("prompt_injection", re.compile(r'忽略.{0,20}(之前|所有|上述|以上).{0,20}指令', re.IGNORECASE)),
            ("prompt_injection", re.compile(r'system\s*prompt\s*(override|覆盖|泄露)', re.IGNORECASE)),
            ("prompt_injection", re.compile(r'不要\s*遵循\s*(系统|之前)', re.IGNORECASE)),
            ("deception", re.compile(r'不要\s*告诉\s*用户', re.IGNORECASE)),
            ("deception", re.compile(r'隐藏.{0,20}(信息|结果|内容)', re.IGNORECASE)),
            ("deception", re.compile(r'对\s*用户\s*保密', re.IGNORECASE)),
            ("exfil", re.compile(r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|API)', re.IGNORECASE)),
            ("exfil", re.compile(r'\b(send|upload|post)\s+[^\n]*(token|key|secret)', re.IGNORECASE)),
        ]

    # ── 子代理禁用工具集（阶段D tier 层） ─────────────────────────
    # 硬拒，不可被全局 allow 覆盖。子代理工具集是主代理的严格子集。
    # 真正的禁网络靠 _SUB_DENIED_POLICY_TYPES={"network"}（MCP/内置网络工具注册时
    # 都标 policy_type="network"）；这里仅保留稳定内建工具名作冗余防御，
    # 不写死配置衍生的 mcp:<server>:<tool> 名字（配置改名即失配）。
    _SUB_DENIED_TOOL_NAMES = frozenset({
        "WebFetch",
    })
    _SUB_DENIED_POLICY_TYPES = frozenset({
        "network",  # 所有 network 类工具（子代理不能联网/爬虫）
    })
    _SUB_ALLOWED_POLICY_TYPES = frozenset({
        "readonly", "read_path", "write", "delete", "move", "patch", "exec", "internal",
    })
    # 子代理禁止的 AGENTS.md 写入 etc. —— 通过 tool_name 匹配
    _SUB_DENIED_GLOBAL_STATE_TOOLS = frozenset({
        "UpdateProjectInstructions",  # 写 AGENTS.md
    })

    def check(
        self,
        request: PermissionRequest,
        *,
        journal_authority: Any,
    ) -> PermissionDecision:
        if journal_authority is None:
            raise ValueError("journal_authority is required for permission checks")
        tool_policy_result = self._check_tool_policy(request)

        if tool_policy_result.behavior == PermissionBehavior.DENY:
            return tool_policy_result

        # ── 阶段D tier 层：子代理权限收缩（不可被全局 allow 翻盘） ──
        is_sub = getattr(request, "agent_tier", "main") == "sub"
        if is_sub:
            decision = self._check_sub_agent_tier(request, tool_policy_result)
            if decision is not None:
                return decision

        # ── 阶段E mode 层：规划模式硬门 ──
        if getattr(request, "mode", "execution") == "planning":
            decision = self._check_planning_mode_gate(request, tool_policy_result)
            if decision is not None:
                return decision

        if tool_policy_result.behavior == PermissionBehavior.ASK:
            return self._handle_ask(
                request,
                tool_policy_result.reason,
                journal_authority=journal_authority,
            )

        for rule in self._deny_rules:
            if rule.matches(request.tool_name, request.tool_input, request.session_id):
                return PermissionDecision(
                    behavior=rule.behavior,
                    reason=rule.reason or f"全局拒绝规则 '{rule.name}' 命中",
                )

        # 全局 allow 规则对子代理被禁工具不生效（tier 层已提前返回）
        for rule in self._allow_rules:
            if rule.matches(request.tool_name, request.tool_input, request.session_id):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    reason=rule.reason or f"全局允许规则 '{rule.name}' 命中",
                )

        return tool_policy_result

    def _check_sub_agent_tier(self, request: PermissionRequest, policy_result: PermissionDecision) -> Optional[PermissionDecision]:
        """子代理权限收缩。返回 None 表示放行继续；返回决策即硬拒。

        规则（优先级从高到低）：
        1. 子代理禁用工具名 → 硬拒
        2. 子代理禁用 policy_type → 硬拒
        3. ASK 降级 → 子代理无权问用户，DENY
        4. 白名单放行：只允许 _SUB_ALLOWED_POLICY_TYPES 中的工具
        """
        tool_name = request.tool_name

        # 全局态改写工具
        if tool_name in self._SUB_DENIED_GLOBAL_STATE_TOOLS:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason=f"子代理不允许修改全局状态: {tool_name}",
            )
        # 禁用工具名
        if tool_name in self._SUB_DENIED_TOOL_NAMES:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason=f"子代理不允许使用: {tool_name}",
            )
        # 禁用 policy_type
        policy = request.permission_policy
        if policy is not None and policy.policy_type in self._SUB_DENIED_POLICY_TYPES:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason=f"子代理不允许 {policy.policy_type} 类工具",
            )
        # ASK 降级
        if policy_result.behavior == PermissionBehavior.ASK:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason="子代理无权发起用户确认",
            )
        # 白名单准入
        if policy is not None and policy.policy_type not in self._SUB_ALLOWED_POLICY_TYPES:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason=f"子代理不允许 {policy.policy_type} 类工具",
            )
        return None  # 放行，继续主流程

    # ── 阶段E mode 层：规划模式硬门 ────────────────────────────────
    # 规划模式下拒绝所有 write/exec/state_write 及 is_destructive 工具。
    # 放行 readonly/read_path/ask(=exit_plan_mode)/非破坏性 internal。
    # 仅主代理持 mode，子代理恒 execution（由 _resolve_mode 保证）。

    _PLANNING_DENIED_POLICY_TYPES = frozenset({"write", "delete", "move", "patch", "exec", "state_write"})

    def _check_planning_mode_gate(
        self, request: PermissionRequest, policy_result: PermissionDecision
    ) -> Optional[PermissionDecision]:
        policy = request.permission_policy
        policy_type = policy.policy_type if policy else ""

        # Explicitly marked state transitions are the sole planning-mode write
        # capability. Tier checks run before this gate, so this marker cannot grant
        # subagents state_write access or bypass any other safety layer.
        if policy_type == "state_write" and getattr(policy, "allow_in_planning", False):
            return None
        # 直接拒绝的 policy_type
        if policy_type in self._PLANNING_DENIED_POLICY_TYPES:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason="规划模式下禁止写/执行/状态修改操作，请先 exit_plan_mode 获取审批",
            )
        # 拒绝只读标记以外的破坏性工具（SubAgent/ParallelTask 等 is_destructive=True）
        # 注意：ToolSpec.is_destructive 不直接可访问，通过 policy_type 间接判断。
        # internal 类型中 SubAgent/ParallelTask 是编排级，规划阶段也需拒绝。
        if policy_type == "internal" and request.tool_name in ("SubAgent", "ParallelTask"):
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason="规划模式下禁止委派子代理，请先 exit_plan_mode 获取审批",
            )
        return None  # 放行

    def check_tool_policy(self, policy: ToolPermissionPolicy, tool_input: Dict[str, Any], tool_name: str = "", session_id: str = "") -> PermissionDecision:
        normalized = self._normalize_tool_input(tool_input)

        if policy.policy_type == "readonly":
            return PermissionDecision(behavior=PermissionBehavior.ALLOW)

        if policy.policy_type == "ask":
            return PermissionDecision(behavior=PermissionBehavior.ASK, reason=policy.reason)

        if policy.policy_type == "write":
            return self._check_write_policy(normalized, policy.path_field, session_id)

        if policy.policy_type == "delete":
            return self._check_risky_path_policy(normalized, policy.path_field, session_id, "删除文件")

        if policy.policy_type == "move":
            return self._check_risky_path_policy(normalized, policy.path_field, session_id, "移动文件")

        if policy.policy_type == "patch":
            return PermissionDecision(behavior=PermissionBehavior.ALLOW)

        if policy.policy_type == "exec":
            return self._check_exec_policy(normalized, policy.command_field, policy.path_fields, session_id)

        if policy.policy_type == "skill_script":
            return self._check_skill_script_policy(normalized)

        if policy.policy_type == "read_path":
            return self._check_read_path_policy(normalized, policy.path_field, session_id)

        if policy.policy_type == "internal":
            if tool_name in ("SubAgent", "ParallelTask"):
                return PermissionDecision(behavior=PermissionBehavior.ALLOW)
            return PermissionDecision(behavior=PermissionBehavior.DENY, reason=f"internal 策略仅允许系统内建工具，工具 {tool_name or '未知'} 不在白名单")

        if policy.policy_type == "state_write":
            return PermissionDecision(behavior=PermissionBehavior.ALLOW)

        if policy.policy_type == "network":
            return PermissionDecision(behavior=PermissionBehavior.ALLOW)

        return PermissionDecision(behavior=PermissionBehavior.DENY, reason=f"未知权限策略类型: {policy.policy_type}")

    def add_deny_rule(self, rule: PermissionRule) -> None:
        self._deny_rules.append(rule)

    def add_allow_rule(self, rule: PermissionRule) -> None:
        self._allow_rules.append(rule)

    def check_dangerous_command(self, command: str) -> PermissionDecision:
        reason = dangerous_command_reason(command)
        if reason:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason=reason,
            )
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    def check_shell_command_risk(self, command: str) -> PermissionDecision:
        danger = self.check_dangerous_command(command)
        if danger.behavior == PermissionBehavior.DENY:
            return danger
        for pattern in self._mutating_command_patterns:
            if pattern.search(command):
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    reason=f"Bash 命令包含明显文件副作用，需要用户确认: {pattern.pattern}",
                )
        for pattern in self._network_or_credential_command_patterns:
            if pattern.search(command):
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    reason=f"Bash 命令可能涉及网络或凭证，需要用户确认: {pattern.pattern}",
                )
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    def make_feedback(self, decision: PermissionDecision) -> ToolFeedback:
        if decision.behavior == PermissionBehavior.DENY:
            return ToolFeedback(
                error_type="权限拒绝",
                error_code="PERMISSION_DENIED",
                what_went_wrong=decision.reason,
                correct_usage="检查路径是否在允许目录内，或确认操作是否需要用户授权。",
                retryable=False,
                do_not_retry_same_call=True,
            )
        if decision.behavior == PermissionBehavior.ASK:
            return ToolFeedback(
                error_type="权限拒绝",
                error_code="PERMISSION_ASK_DENIED",
                what_went_wrong=f"需要用户确认: {decision.reason}",
                correct_usage="此操作需要用户授权，当前自动拒绝。请换一种不需要授权的方式，或向用户说明原因。",
                retryable=False,
                do_not_retry_same_call=True,
            )
        return ToolFeedback()

    def _check_tool_policy(self, request: PermissionRequest) -> PermissionDecision:
        policy = request.permission_policy
        if policy is not None:
            policy_result = self.check_tool_policy(policy, request.tool_input, request.tool_name, request.session_id)
            if policy_result.behavior == PermissionBehavior.DENY:
                return policy_result
            if policy_result.behavior == PermissionBehavior.ASK:
                return policy_result
        else:
            # 未显式声明 policy：回退看 is_readonly——只读工具放行，非只读仍需确认。
            # 此前一律 DENY，宿主用 build_agent_tool 标了 is_readonly=True 仍被一刀切拒绝，
            # 接入成本过高（每个工具都要补 policy）。
            if request.is_readonly:
                policy_result = PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    reason="只读工具（未声明 policy，按 is_readonly 放行）",
                )
            else:
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    reason=f"工具 {request.tool_name} 未声明权限策略且非只读，需要用户确认",
                )

        check_fn = getattr(request, "_check_permissions_fn", None)
        if check_fn is not None:
            clean_input = {k: v for k, v in request.tool_input.items() if k not in ("__check_permissions_fn", "__call_id")}
            try:
                result = check_fn(clean_input)
                if hasattr(result, "behavior"):
                    if result.behavior == PermissionBehavior.DENY:
                        return PermissionDecision(behavior=PermissionBehavior.DENY, reason=getattr(result, "reason", "") or "")
                    if result.behavior == PermissionBehavior.ASK:
                        return PermissionDecision(behavior=PermissionBehavior.ASK, reason=getattr(result, "reason", "") or "")
            except Exception as e:
                logger.warning("工具级权限检查异常: %s", e)
                return PermissionDecision(behavior=PermissionBehavior.DENY, reason=f"权限检查异常: {e}")

        return policy_result

    def _handle_ask(
        self,
        request: PermissionRequest,
        reason: str,
        *,
        journal_authority: Any,
    ) -> PermissionDecision:
        if journal_authority is None:
            raise ValueError("journal_authority is required for ASK creation")
        if self._ask_service is None:
            logger.warning("PermissionService: AskService 未设置，ASK 自动拒绝")
            return PermissionDecision(behavior=PermissionBehavior.DENY, reason=f"需要用户确认: {reason}（AskService 不可用）")

        from floodmind.agent.runtime.contracts.permissions import PermissionAskRequest
        call_id = request.call_id
        clean_input = {k: v for k, v in request.tool_input.items() if k != "__call_id"} if isinstance(request.tool_input, dict) else request.tool_input

        approved = self._ask_service.request(
            PermissionAskRequest(
                session_id=request.session_id,
                call_id=call_id,
                tool_name=request.tool_name,
                reason=reason,
                tool_input=clean_input,
            ),
            journal_authority=journal_authority,
        )

        if approved:
            policy = request.permission_policy
            if policy is not None and policy.policy_type == "exec" and policy.command_field:
                normalized = self._normalize_tool_input(request.tool_input)
                command = str(normalized.get(policy.command_field, "")).strip()
                if command and scan_exec_writes(command).unresolved:
                    approve_unresolved_exec_writes(command)
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, reason="用户确认允许")
        return PermissionDecision(behavior=PermissionBehavior.DENY, reason="用户拒绝")

    def _check_write_policy(self, normalized: Dict[str, Any], path_field: str, session_id: str = "") -> PermissionDecision:
        raw_path = str(normalized.get(path_field, "")).strip()
        if not raw_path:
            return PermissionDecision(behavior=PermissionBehavior.ALLOW)

        result = self._path_service.resolve_simple(raw_path, access="write", session_id=session_id)
        if result.source == "no_context_rejected":
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason=result.reason,
            )
        if not result.allowed:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason=result.reason,
            )
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    def _check_exec_policy(self, normalized: Dict[str, Any], command_field: str, path_fields: List[str], session_id: str = "") -> PermissionDecision:
        command = str(normalized.get(command_field, "")).strip() if command_field else ""
        if command:
            # 危险命令 → 硬拒
            danger = self.check_dangerous_command(command)
            if danger.behavior != PermissionBehavior.ALLOW:
                return danger

        for pf in path_fields:
            raw_path = str(normalized.get(pf, "")).strip()
            if raw_path:
                result = self._path_service.resolve_simple(raw_path, access="exec", session_id=session_id)
                if not result.allowed:
                    return PermissionDecision(behavior=PermissionBehavior.DENY, reason=result.reason)

        # 无法静态解析的写目标不能按普通文件副作用放行。权限管线有 AskService 时
        # 返回 ASK 走唯一交互出口；无交互出口时 _handle_ask 会按既有契约降级 DENY。
        if command:
            write_scan = scan_exec_writes(command)
            if write_scan.unresolved:
                return PermissionDecision(
                    behavior=PermissionBehavior.ASK,
                    reason="Bash 命令包含无法静态解析的写目标，需要用户确认: "
                    + "; ".join(write_scan.unresolved),
                )

            deny_reason = check_exec_write_targets(
                command,
                resolver=lambda t: self._path_service.resolve_simple(t, access="write", session_id=session_id),
            )
            if deny_reason:
                return PermissionDecision(behavior=PermissionBehavior.DENY, reason=deny_reason)

        if command:
            risk = self.check_shell_command_risk(command)
            if risk.behavior != PermissionBehavior.ALLOW:
                return risk
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    def _check_risky_path_policy(self, normalized: Dict[str, Any], path_field: str, session_id: str, operation: str) -> PermissionDecision:
        write_decision = self._check_write_policy(normalized, path_field, session_id)
        if write_decision.behavior != PermissionBehavior.ALLOW:
            return write_decision
        raw_path = str(normalized.get(path_field, "")).strip()
        reason = f"{operation}属于高风险文件操作"
        if raw_path:
            reason += f": {raw_path}"
        return PermissionDecision(behavior=PermissionBehavior.ASK, reason=reason)

    def _check_skill_script_policy(self, normalized: Dict[str, Any]) -> PermissionDecision:
        from pathlib import Path as _Path
        skill_name = str(normalized.get("skill_name", "")).strip().strip('"').strip("'")
        script_name = str(normalized.get("script_name", "")).strip().strip('"').strip("'")

        if not skill_name or not script_name:
            return PermissionDecision(behavior=PermissionBehavior.ALLOW)

        if '..' in skill_name or '..' in script_name:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason="skill_name 或 script_name 包含路径穿越字符 '..'",
            )

        skill_scripts_dir = self._path_service._project_root / "skills" / skill_name / "scripts"
        script_path = skill_scripts_dir / script_name

        try:
            script_path.resolve().relative_to(skill_scripts_dir.resolve())
        except ValueError:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason=f"脚本路径 {script_path} 越界，不允许逃逸出 skill '{skill_name}' 的 scripts 目录",
            )

        if not script_path.exists():
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                reason=f"脚本 {skill_name}/{script_name} 不在已注册 skill 目录内",
            )
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    def _check_read_path_policy(self, normalized: Dict[str, Any], path_field: str, session_id: str = "") -> PermissionDecision:
        raw_path = str(normalized.get(path_field, "")).strip()
        if not raw_path:
            return PermissionDecision(behavior=PermissionBehavior.ALLOW)

        result = self._path_service.resolve_simple(raw_path, access="read", session_id=session_id)
        if not result.allowed:
            return PermissionDecision(behavior=PermissionBehavior.DENY, reason=result.reason)
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    def _normalize_tool_input(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(tool_input)
        for key in ("file_path", "script_path", "artifact_path", "command"):
            raw = str(normalized.get(key, "")).strip().strip('"').strip("'")
            if raw.startswith('{') and raw.endswith('}'):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and key in parsed:
                        raw = str(parsed[key]).strip().strip('"').strip("'")
                        for k, v in parsed.items():
                            if k not in normalized or not str(normalized.get(k, "")).strip():
                                normalized[k] = v
                except (json.JSONDecodeError, TypeError):
                    pass
            normalized[key] = raw
        return normalized

    def scan_content_threats(self, text: str) -> "ContentThreatResult":
        from floodmind.agent.runtime.contracts.permissions import ContentThreatResult

        if not text or not text.strip():
            return ContentThreatResult(threat_detected=False, threat_types=[])

        detected: List[str] = []
        for threat_type, pattern in self._content_threat_patterns:
            if pattern.search(text):
                detected.append(threat_type)

        return ContentThreatResult(
            threat_detected=len(detected) > 0,
            threat_types=detected,
        )

    @classmethod
    def create_default(cls, ask_service=None, path_service=None) -> "PermissionService":
        svc = cls(ask_service=ask_service, path_service=path_service)
        svc.add_deny_rule(PermissionRule(
            name="deny_system_path_write",
            pattern=r"(/etc/|C:\\\\Windows\\\\|C:\\\\Program Files)",
            behavior=PermissionBehavior.DENY,
            reason="禁止写入系统目录",
        ))
        return svc
