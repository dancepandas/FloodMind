"""Tests for exec command write-target static scanner (SDK 收敛项 ①)."""

from floodmind.agent.runtime.services.exec_write_scanner import (
    approve_unresolved_exec_writes,
    check_exec_write_targets,
    dangerous_command_reason,
    extract_write_targets,
    scan_exec_writes,
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

    def test_relative_in_workspace_name_is_extracted_for_boundary_check(self):
        assert extract_write_targets("echo hi > out.txt") == ["out.txt"]

    def test_powershell_literal_variable_is_resolved(self):
        scan = scan_exec_writes('$target = "C:\\ext\\out.txt"; Set-Content -Path $target x')
        assert scan.targets == ("C:\\ext\\out.txt",)
        assert scan.unresolved == ()

    def test_powershell_dynamic_variable_is_unresolved(self):
        scan = scan_exec_writes("Set-Content -Path $target x")
        assert scan.targets == ()
        assert scan.unresolved

    def test_powershell_command_wrapper_is_scanned(self):
        assert extract_write_targets(
            'powershell -Command "Set-Content -Path C:\\ext\\wrapped.txt x"'
        ) == ["C:\\ext\\wrapped.txt"]
    def test_fd_duplication_is_not_a_write_target(self):
        assert scan_exec_writes("pytest -q 2>&1").unresolved == ()
        assert scan_exec_writes("echo hi >/dev/null 2>&1").unresolved == ()

    def test_obvious_shell_writers(self):
        assert extract_write_targets("touch outside.txt") == ["outside.txt"]
        assert extract_write_targets("echo hi | tee outside.txt") == ["outside.txt"]

    def test_python_inline_literal_write(self):
        assert extract_write_targets("python -c \"open('/tmp/out.txt','w').write('x')\"") == ["/tmp/out.txt"]

    def test_python_inline_dynamic_write_is_unresolved(self):
        assert scan_exec_writes("python -c \"open(target,'w').write('x')\"").unresolved


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

    def test_unresolved_write_target_fails_closed(self):
        deny = check_exec_write_targets(
            "Set-Content -Path $target x",
            resolver=lambda t: _FakeResult(allowed=True),
        )
        assert deny is not None
        assert "无法静态解析" in deny

    def test_approved_unresolved_write_is_consumed_once(self):
        command = "Set-Content -Path $target x"
        approve_unresolved_exec_writes(command)
        assert check_exec_write_targets(
            command,
            resolver=lambda t: _FakeResult(allowed=True),
            allow_approved_unresolved=True,
        ) is None
        assert check_exec_write_targets(
            command,
            resolver=lambda t: _FakeResult(allowed=True),
            allow_approved_unresolved=True,
        ) is not None

    def test_dangerous_union_includes_stricter_handler_rules(self):
        assert dangerous_command_reason("chmod -R 777 /tmp/x")
        assert dangerous_command_reason("pip uninstall floodmind")

    def test_resolver_exception_fails_closed(self):
        deny = check_exec_write_targets(
            "echo hi > out.txt",
            resolver=lambda t: (_ for _ in ()).throw(RuntimeError("bad path")),
        )
        assert deny is not None
        assert "路径解析失败" in deny
