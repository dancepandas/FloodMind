"""Tests for stage E: plan/execution mode hard gate.

Uses exec policy (safe commands without path checks) to ensure
tool-level checks pass and the mode gate is reached.
"""

import pytest

from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior,
    PermissionRequest,
    ToolPermissionPolicy,
)
from floodmind.agent.runtime.services.permission_service import PermissionService


@pytest.fixture
def perm_svc():
    return PermissionService()


class TestPlanModeGate:

    def test_write_denied_in_planning_mode(self, perm_svc):
        """写/执行类 policy 在规划模式下被硬门拒绝。使用 exec 避免路径校验干扰。"""
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "echo hello"},
            permission_policy=ToolPermissionPolicy(policy_type="exec"),
            mode="planning",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.DENY
        assert "规划模式" in decision.reason

    def test_read_allowed_in_planning_mode(self, perm_svc):
        request = PermissionRequest(
            tool_name="Read",
            tool_input={"file_path": "test.txt"},
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
            mode="planning",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.ALLOW

    def test_exec_denied_in_planning_mode(self, perm_svc):
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "echo hello"},
            permission_policy=ToolPermissionPolicy(policy_type="exec"),
            mode="planning",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.DENY
        assert "规划模式" in decision.reason

    def test_subagent_denied_in_planning_mode(self, perm_svc):
        request = PermissionRequest(
            tool_name="SubAgent",
            tool_input={"task": "do something"},
            permission_policy=ToolPermissionPolicy(policy_type="internal"),
            mode="planning",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.DENY
        assert "规划模式" in decision.reason

    def test_parallel_task_denied_in_planning_mode(self, perm_svc):
        request = PermissionRequest(
            tool_name="ParallelTask",
            tool_input={"tasks": []},
            permission_policy=ToolPermissionPolicy(policy_type="internal"),
            mode="planning",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.DENY

    def test_create_plan_allowed_in_planning_mode(self, perm_svc):
        request = PermissionRequest(
            tool_name="create_plan",
            tool_input={"user_goal": "test", "deliverables": "x", "steps": []},
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
            mode="planning",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.ALLOW

    def test_exit_plan_mode_not_blocked_by_mode_gate(self, perm_svc):
        """exit_plan_mode 是 ask 类型，不会被规划模式硬门拒绝（但最终行为取决于 AskService）。"""
        request = PermissionRequest(
            tool_name="exit_plan_mode",
            tool_input={"plan_summary": "plan content"},
            permission_policy=ToolPermissionPolicy(policy_type="ask", reason="审批"),
            mode="planning",
        )
        decision = perm_svc.check(request)
        # mode gate 放行 ASK → 最终行为是 ASK 或 DENY（取决于 AskService 是否注入）
        # 但不应包含"规划模式"拒绝原因
        assert "规划模式" not in decision.reason

    def test_execution_mode_no_restriction(self, perm_svc):
        """execution 模式下正常放行"""
        request = PermissionRequest(
            tool_name="Read",
            tool_input={"file_path": "test.txt"},
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
            mode="execution",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.ALLOW

    def test_planning_mode_does_not_restrict_sub_agent(self, perm_svc):
        """子代理恒 execution，工具级检查通过即可"""
        request = PermissionRequest(
            tool_name="Read",
            tool_input={"file_path": "test.txt"},
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
            agent_tier="sub",
            mode="execution",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.ALLOW
