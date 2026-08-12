"""SDK purity and legacy adapter boundary tests."""

import importlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path


# 已弃用并删除：web (Flask/waitress/...) 和 tui (textual) 包。
# banned SDK 导入：模块路径、依赖名都从 SDK 根命名空间彻底剥离。
BANNED_SDK_IMPORTS = ("floodmind.server", "floodmind.tui", "flask", "textual")
BANNED_CORE_DEPS = (
    "flask", "flask-cors", "textual", "waitress", "gunicorn",
    "websockets", "httpx-sse", "rich",
)
# 已弃用的 web/tui 可选 extras 不应再出现。
BANNED_OPTIONAL_EXTRAS = ("web", "tui", "legacy")


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
    for forbidden in BANNED_OPTIONAL_EXTRAS:
        assert forbidden not in extras, f"弃用的 {forbidden} extra 必须从 pyproject 移除"


def test_packaging_metadata_is_platform_safe_and_deployment_is_explicit():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]

    assert any("docx2pdf" in dep and "platform_system" in dep for dep in extras["doc"])
    assert "deployment" in extras
    # deployment 不再拉入 web extras（web 已弃用移除）
    assert "floodmind[web" not in extras["deployment"]
    # gpu extra 已移除（chronos 外置 MCP，核心不引 torch/transformers）
    assert "floodmind[gpu" not in extras["deployment"]
    assert "floodmind[doc]" in extras["deployment"]

    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    assert 'comtypes>=1.4.0; platform_system == "Windows"' in requirements
    assert 'pywin32>=306; platform_system == "Windows"' in requirements
    # requirements.txt 不再引用 web/tui extras
    assert "floodmind[web]" not in requirements
    assert "floodmind[tui]" not in requirements
    assert "floodmind[legacy]" not in requirements

    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    # 不再 install web/tui extras
    assert "floodmind[gpu,doc,web]" not in dockerfile
    # 不再启动 Flask/waitress web_server
    assert "web_server:app" not in dockerfile
    assert "-r requirements.txt" not in dockerfile


def test_manifest_does_not_reference_missing_license_file():
    manifest = Path("MANIFEST.in").read_text(encoding="utf-8")
    assert "include LICENSE" not in manifest


def test_imports_are_side_effect_free_in_isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    script = """
import json, socket, ssl, sys
before_dns = socket.getaddrinfo
before_tls = ssl._create_default_https_context
import floodmind
from floodmind.config import settings
from floodmind import Agent
print(json.dumps({
    "matplotlib": "matplotlib" in sys.modules,
    "dns_same": socket.getaddrinfo is before_dns,
    "tls_same": ssl._create_default_https_context is before_tls,
}))
"""
    env = os.environ.copy()
    env.update({"HOME": str(home), "USERPROFILE": str(home), "HF_HOME": str(home / "hf")})
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    observed = json.loads(result.stdout.strip())
    assert observed == {"matplotlib": False, "dns_same": True, "tls_same": True}
    assert not (home / ".floodmind").exists()
    assert not (home / "hf").exists()


def test_plotting_configuration_is_explicit_and_idempotent():
    script = """
import json, sys
import floodmind.plotting as plotting
before = "matplotlib" in sys.modules
plotting.configure_plotting()
first = sys.modules["matplotlib"].rcParams["figure.dpi"]
plotting.configure_plotting()
print(json.dumps({"before": before, "dpi": first}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], text=True, capture_output=True, check=True
    )
    assert json.loads(result.stdout.strip()) == {"before": False, "dpi": 300.0}


def test_sitecustomize_is_not_packaged():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert "py-modules" not in data.get("tool", {}).get("setuptools", {})
    manifest = Path("MANIFEST.in").read_text(encoding="utf-8")
    assert "sitecustomize.py" not in manifest


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
