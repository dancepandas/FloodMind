"""SDK purity and legacy adapter boundary tests."""

import importlib
import sys
import tomllib
from pathlib import Path


BANNED_SDK_IMPORTS = ("floodmind.server", "floodmind.tui", "flask", "textual")
BANNED_CORE_DEPS = ("flask", "flask-cors", "textual", "waitress", "gunicorn", "websockets", "httpx-sse")


def _forget(names):
    for name in names:
        sys.modules.pop(name, None)


def test_import_floodmind_keeps_legacy_web_tui_unloaded():
    _forget(BANNED_SDK_IMPORTS)

    import floodmind

    assert floodmind.Agent is not None
    for name in BANNED_SDK_IMPORTS:
        assert name not in sys.modules


def test_pyproject_core_dependencies_exclude_web_tui_stack():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    deps = "\n".join(data["project"]["dependencies"]).lower()

    for forbidden in BANNED_CORE_DEPS:
        assert forbidden not in deps

    extras = data["project"]["optional-dependencies"]
    assert "web" in extras
    assert "tui" in extras
    assert "legacy" in extras


def test_runtime_adapters_import_without_flask():
    _forget(("flask",))

    modules = [
        "floodmind.agent.runtime.adapters.permission_api",
        "floodmind.agent.runtime.adapters.checkpoint_api",
        "floodmind.agent.runtime.adapters.tracing_api",
        "floodmind.agent.runtime.adapters.event_stream_adapter",
        "floodmind.agent.runtime.adapters.flask_permission_api",
        "floodmind.agent.runtime.adapters.flask_checkpoint_api",
        "floodmind.agent.runtime.adapters.flask_tracing_api",
        "floodmind.agent.runtime.adapters.sse_stream_adapter",
    ]
    for module in modules:
        importlib.import_module(module)

    assert "flask" not in sys.modules
