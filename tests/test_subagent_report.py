"""Tests for SubAgentReport contract."""

from floodmind.agent.runtime.contracts.subagent import SubAgentReport


class TestSubAgentReport:
    def test_to_payload_omits_full_tool_results(self):
        report = SubAgentReport(
            summary="子任务已完成",
            completed=True,
            outputs={"key": "value"},
            artifacts=["data/sessions/sub-xxx/outputs/report.md"],
            next_steps=["验证报告"],
            needs_human=False,
            sub_session_id="sub-parent-delegate-abc12345",
            tool_result_summaries=[
                {"tool_name": "Read", "status": "completed", "summary": "读取了文件"},
                {"tool_name": "Bash", "status": "completed", "summary": "运行了脚本"},
            ],
        )

        payload = report.to_payload()

        assert payload["summary"] == "子任务已完成"
        assert payload["completed"] is True
        assert payload["artifacts"] == ["data/sessions/sub-xxx/outputs/report.md"]
        assert payload["next_steps"] == ["验证报告"]
        assert payload["needs_human"] is False
        # 父代理 payload 不应包含 tool_result_summaries 等完整信息
        assert "tool_result_summaries" not in payload
        assert "sub_session_id" not in payload
        assert "outputs" not in payload

    def test_default_values(self):
        report = SubAgentReport(summary="empty")
        assert report.completed is False
        assert report.outputs == {}
        assert report.artifacts == []
        assert report.next_steps == []
        assert report.needs_human is False
        assert report.sub_session_id == ""
        assert report.tool_result_summaries == []
