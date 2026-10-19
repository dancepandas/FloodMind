"""G-06a：子进程/沙箱环境凭证隔离。"""

import os
from pathlib import Path

import pytest

from floodmind.agent.runtime.contracts.sandbox import SandboxPolicy
from floodmind.agent.runtime.services.credential_env import (
    apply_env_policy,
    is_credential_env_key,
    strip_credential_env,
)
from floodmind.agent.runtime.services.process_sandbox import ProcessSandbox
from floodmind.agent.runtime.services.sandbox_backend import LocalRestrictedSandbox


@pytest.mark.parametrize(
    "key",
    [
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "FLOODMIND_API_KEY",
        "HF_TOKEN",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "DB_PASSWORD",
        "MYSQL_PASSWD",
        "SSH_AUTH_SOCK",
        "MY_CREDENTIALS",
        "PRIVATE_KEY",
        "SESSION_TOKEN",
        "API_KEY",
        "COOKIE",
    ],
)
def test_credential_names_are_detected(key):
    assert is_credential_env_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "PATH",
        "PYTHONPATH",
        "PYTHONUTF8",
        "TOKENIZERS_PARALLELISM",
        "KEYBOARD_LAYOUT",
        "SESSION_ID",
        "COMPASS_HOME",
        "LANG",
        "HTTP_PROXY",
    ],
)
def test_ordinary_names_are_preserved(key):
    assert not is_credential_env_key(key)


class TestStripCredentialEnv:
    def test_removes_credentials_and_keeps_plain_vars(self):
        env = {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "DB_PASSWORD": "p",
            "TOKENIZERS_PARALLELISM": "false",
        }

        safe = strip_credential_env(env)

        assert safe["PATH"] == "/usr/bin"
        assert safe["TOKENIZERS_PARALLELISM"] == "false"
        assert "OPENAI_API_KEY" not in safe
        assert "AWS_SECRET_ACCESS_KEY" not in safe
        assert "DB_PASSWORD" not in safe
        # 不修改入参
        assert "OPENAI_API_KEY" in env

    def test_apply_env_policy_allowlist_and_explicit_inject(self):
        env = {"PATH": "/bin", "OPENAI_API_KEY": "sk", "LANG": "C"}
        policy_env = apply_env_policy(
            env,
            allowlist=["LANG"],
            required=["PATH"],
            inject={"MCP_SERVER_TOKEN": "declared-by-config"},
        )

        assert policy_env == {
            "PATH": "/bin",
            "LANG": "C",
            "MCP_SERVER_TOKEN": "declared-by-config",
        }


class TestExecBashEnv:
    def test_build_exec_env_drops_host_credentials(self, monkeypatch):
        from floodmind.tools.base_tools import _build_exec_env

        monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-should-not-leak")
        monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")

        env = _build_exec_env()

        assert "OPENAI_API_KEY" not in env
        assert "DEEPSEEK_API_KEY" not in env
        assert env["TOKENIZERS_PARALLELISM"] == "false"
        assert env["PYTHONUTF8"] == "1"


class TestProcessSandboxEnv:
    def test_restrict_env_drops_credentials_and_home(self, tmp_path):
        sandbox = ProcessSandbox(workspace_dir=tmp_path)
        try:
            env = sandbox.restrict_env(
                {
                    "PATH": "/bin",
                    "HOME": "/home/user",
                    "USERPROFILE": r"C:\Users\user",
                    "ANTHROPIC_API_KEY": "sk",
                    "TMP": "/tmp/old",
                },
                tmp_path,
            )
        finally:
            sandbox.close()

        assert "PATH" in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "HOME" not in env
        assert "USERPROFILE" not in env
        assert env["TMP"] == str(tmp_path)


class TestSandboxBackendEnv:
    def test_build_env_filters_credentials_without_allowlist(self, tmp_path):
        backend = LocalRestrictedSandbox()
        policy = SandboxPolicy(file_root=str(tmp_path))

        env = backend._build_env(
            {
                "PATH": "/bin",
                "OPENAI_API_KEY": "sk",
                "HF_TOKEN": "hf",
                "HOME": "/home/user",
            },
            policy,
            Path(tmp_path),
        )

        assert env["PATH"] == "/bin"
        assert "OPENAI_API_KEY" not in env
        assert "HF_TOKEN" not in env
        assert "HOME" not in env

    def test_secret_inject_is_explicit_authorization(self, tmp_path):
        backend = LocalRestrictedSandbox()
        policy = SandboxPolicy(
            file_root=str(tmp_path),
            env_allowlist=["PATH"],
            secret_inject={"DECLARED_TOKEN": "allowed"},
        )

        env = backend._build_env(
            {"PATH": "/bin", "OPENAI_API_KEY": "sk"},
            policy,
            Path(tmp_path),
        )

        assert env == {
            "PATH": "/bin",
            "TEMP": str(tmp_path / "tmp"),
            "TMP": str(tmp_path / "tmp"),
            "TMPDIR": str(tmp_path / "tmp"),
            "DECLARED_TOKEN": "allowed",
        }
