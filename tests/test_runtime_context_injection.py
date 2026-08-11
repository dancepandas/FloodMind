import contextvars
import subprocess
import sys
from pathlib import Path

from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.tools.session_context import get_runtime_context, set_runtime_context


def test_runtime_context_roundtrip():
    rtc = RuntimeContext(
        conversation_id="conv_1",
        task_id="task_1",
        run_id="run_1",
        thread_id="thread_1",
        turn_id="turn_1",
        agent_tier="main",
    )
    set_runtime_context(rtc)
    assert get_runtime_context() == rtc


def test_get_runtime_context_default_none():
    from floodmind.tools import session_context as sc

    token = sc._session_ctx_var.set({})
    try:
        assert get_runtime_context() is None
    finally:
        sc._session_ctx_var.reset(token)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_legacy_getters_removed():
    banned = {
        "get_permission_service",
        "get_path_service",
        "get_background_task_service",
    }
    for mod in ("permission_service", "path_service", "background_task_service"):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    f"import floodmind.agent.runtime.services.{mod} as m; "
                    "print([x for x in dir(m) if 'service' in x and 'get_' in x])"
                ),
            ],
            capture_output=True,
            text=True,
            cwd=str(_root()),
            check=True,
        )
        assert not banned & set(eval(result.stdout))
