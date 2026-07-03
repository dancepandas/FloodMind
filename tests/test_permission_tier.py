"""Tests for stage D: agent tier-based permission layering (CC harness essence)."""

import pytest

from floodmind.agent.runtime.contracts.permissions import (
    PermissionBehavior,
    PermissionDecision,
    PermissionRequest,
    ToolPermissionPolicy,
)
from floodmind.agent.runtime.services.permission_service import PermissionService


@pytest.fixture
def perm_svc():
    return PermissionService()


class TestSubAgentTier:
    def test_network_denied_for_sub(self, perm_svc):
        request = PermissionRequest(
            tool_name="WebFetch",
            tool_input={"url": "https://example.com"},
            permission_policy=ToolPermissionPolicy(policy_type="network"),
            agent_tier="sub",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.DENY
        assert "子代理" in decision.reason

    def test_readonly_allowed_for_sub(self, perm_svc):
        request = PermissionRequest(
            tool_name="Read",
            tool_input={"file_path": "test.txt"},
            permission_policy=ToolPermissionPolicy(policy_type="readonly"),
            agent_tier="sub",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.ALLOW

    def test_ask_downgraded_for_sub(self, perm_svc):
        """子代理的 ASK 工具降级为 DENY（非全局态改名工具，确保走 ASK 降级分支）"""
        request = PermissionRequest(
            tool_name="SomeAskTool",
            tool_input={},
            permission_policy=ToolPermissionPolicy(policy_type="ask", reason="需要确认"),
            agent_tier="sub",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.DENY
        assert "子代理无权" in decision.reason

    def test_global_state_write_denied_for_sub(self, perm_svc):
        request = PermissionRequest(
            tool_name="UpdateProjectInstructions",
            tool_input={},
            permission_policy=ToolPermissionPolicy(policy_type="state_write"),
            agent_tier="sub",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.DENY
        assert "不允许修改全局状态" in decision.reason

    def test_write_allowed_for_sub_in_range(self, perm_svc):
        """子代理允许 exec 类型（非 write——write 有额外路径校验，范围由 PathService 控制）"""
        request = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "echo hello"},
            permission_policy=ToolPermissionPolicy(policy_type="exec"),
            agent_tier="sub",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.ALLOW

    def test_global_allow_does_not_override_sub_deny(self, perm_svc):
        """全局 allow 规则不能给子代理开 network 后门"""
        from floodmind.agent.runtime.contracts.permissions import PermissionRule
        perm_svc.add_allow_rule(PermissionRule(
            name="allow-all-web",
            tool_name="WebFetch",
            behavior=PermissionBehavior.ALLOW,
            reason="全局允许 WebFetch",
        ))
        request = PermissionRequest(
            tool_name="WebFetch",
            tool_input={"url": "https://example.com"},
            permission_policy=ToolPermissionPolicy(policy_type="network"),
            agent_tier="sub",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.DENY
        assert "子代理" in decision.reason

    def test_main_agent_not_restricted_by_tier(self, perm_svc):
        """主代理不受 tier 层限制"""
        request = PermissionRequest(
            tool_name="WebFetch",
            tool_input={"url": "https://example.com"},
            permission_policy=ToolPermissionPolicy(policy_type="network"),
            agent_tier="main",
        )
        decision = perm_svc.check(request)
        assert decision.behavior == PermissionBehavior.ALLOW
