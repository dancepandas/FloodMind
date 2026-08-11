from floodmind.agent.runtime.contracts.run_state import (
    RunState, RunStatus, PendingApproval,
)
from floodmind.agent.runtime.contracts.tool_transaction import ToolTransaction

def test_default_state():
    s = RunState(run_id="run_1")
    assert s.status == RunStatus.created
    assert s.pending_tool_transactions == []
    assert s.turns == []
    assert s.processed_event_ids == []

def test_required_identity_fields():
    try:
        RunState()  # missing run_id
    except Exception:
        return
    assert False, "run_id should be required"

def test_pending_lists():
    s = RunState(
        run_id="run_1",
        pending_tool_transactions=[ToolTransaction(transaction_id="ttx_1", call_id="call_1", tool_id="builtin:Read")],
        pending_approvals=[PendingApproval(ask_id="ask_1", call_id="call_1", tool_name="Bash")],
    )
    assert s.pending_tool_transactions[0].status.value == "proposed"
    assert s.pending_approvals[0].ask_id == "ask_1"
