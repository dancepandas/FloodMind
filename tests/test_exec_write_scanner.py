"""Tests for exec command write-target static scanner (SDK 收敛项 ①)."""

from floodmind.agent.runtime.services.exec_write_scanner import (
    check_exec_write_targets,
    extract_write_targets,
)


class TestExtractWriteTargets:
    def test_powershell_set_content_flag(self):
        assert extract_write_targets('Set-Content -Path C:\\ext\\file.txt "data"') == ["C:\\ext\\file.txt"]

    def test_powershell_out_file_via_pipeline(self):
        targets = extract_write_targets('echo "x" | Out-File C:\\ext\\out.txt')
        assert "C:\\ext\\out.txt" in targets

    def test_shell_redirect(self):
        assert extract_write_targets("echo hi > C:\\ext\\out.txt") == ["C:\\ext\\out.txt"]

    def test_shell_redirect_quoted_path_with_space(self):
        assert extract_write_targets('echo hi > "C:\\My Files\\out.txt"') == ["C:\\My Files\\out.txt"]

    def test_copy_item_takes_destination(self):
        targets = extract_write_targets("Copy-Item C:\\a\\src.txt C:\\ext\\dst.txt")
        assert "C:\\ext\\dst.txt" in targets
        assert "C:\\a\\src.txt" not in targets

    def test_string_literal_arrow_not_misdetected(self):
        assert extract_write_targets('echo "x > y"') == []

    def test_echoed_cmdlet_text_not_misdetected(self):
        assert extract_write_targets('echo "Set-Content -Path C:\\x"') == []

    def test_null_targets_skipped(self):
        assert extract_write_targets("echo hi > /dev/null") == []

    def test_plain_command_no_targets(self):
        assert extract_write_targets("python script.py") == []

    def test_relative_in_workspace_name_not_extracted(self):
        # 相对文件名落在工作区内、无需拦截 → 不提取
        assert extract_write_targets("echo hi > out.txt") == []


class _FakeResult:
    def __init__(self, allowed, reason=""):
        self.allowed = allowed
        self.reason = reason


class TestCheckExecWriteTargets:
    def test_denies_out_of_roots_target(self):
        deny = check_exec_write_targets(
            "Set-Content -Path C:\\ext\\file.txt x",
            resolver=lambda t: _FakeResult(allowed=False, reason="写入路径 C:\\ext\\file.txt 不在允许目录内"),
        )
        assert deny is not None
        assert "写目标" in deny
        assert "C:\\ext\\file.txt" in deny

    def test_allows_in_roots_target(self):
        deny = check_exec_write_targets(
            "echo hi > C:\\data\\out.txt",
            resolver=lambda t: _FakeResult(allowed=True),
        )
        assert deny is None

    def test_no_targets_passes_even_when_resolver_denies(self):
        assert check_exec_write_targets("python script.py", resolver=lambda t: _FakeResult(allowed=False)) is None
