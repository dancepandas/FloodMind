"""文件工具（Write/Edit/ApplyPatch）编码与行尾回归测试。

覆盖对抗性审查确认的三类缺陷：
- read_text/write_text 默认 newline 翻译把 LF 文件改成 CRLF；
- Edit 的 GBK 回退读入 U+FFFD 后写回失败，但 open('w') 已截断文件（实测可截成 0 字节）；
- ApplyPatch 解析器丢弃空行（含统一 diff 的空上下文行）导致 hunk 计数错位损坏文件。
"""

from floodmind.agent.runtime.contracts.runtime_context import RuntimeContext
from floodmind.agent.runtime.services.path_service import PathService
from floodmind.agent.runtime.services.workspace_service import build_folder_workspace
from floodmind.tools.file_tools import _impl_apply_patch, _impl_edit, _impl_write
from floodmind.tools.session_context import set_runtime_context, set_session_context


def _bind_workspace(tmp_path):
    ws = build_folder_workspace("s1", primary_dir=tmp_path / "project").ensure()
    set_session_context("s1", output_dir=str(ws.user_dir), cwd=str(ws.default_cwd), workspace_dir=str(ws.workspace_dir))
    set_runtime_context(RuntimeContext("s1", "s1", "run", "thread", "turn", path_service=PathService(project_root=tmp_path, workspace=ws)))
    return ws


def _reset(tmp_path):
    set_session_context("", output_dir="")
    set_runtime_context(RuntimeContext("", "", "", "", "", path_service=PathService(project_root=tmp_path)))


# ── Write：行尾保持 + 原子写 ───────────────────────────────────────────────


def test_write_preserves_lf_line_endings(tmp_path):
    """Write 不得做换行翻译：内容中的 LF 原样落盘（旧实现 write_text 会写成 CRLF）。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "lf.txt"
    try:
        result = _impl_write(file_path=str(target), content="第一行\n第二行\n")
    finally:
        _reset(tmp_path)

    assert "成功" in result
    assert target.read_bytes() == "第一行\n第二行\n".encode("utf-8")
    assert b"\r" not in target.read_bytes()


def test_write_leaves_no_temp_files_behind(tmp_path):
    """原子写（同目录临时文件 + os.replace）成功后不得残留 .tmp 文件。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "f.txt"
    try:
        _impl_write(file_path=str(target), content="hello\n")
    finally:
        _reset(tmp_path)

    assert target.read_bytes() == b"hello\n"
    assert not list(ws.workspace_dir.glob("*.tmp")), "原子写残留临时文件"


# ── Edit：行尾保持 + GBK 无损编辑 ──────────────────────────────────────────


def test_edit_preserves_lf_line_endings(tmp_path):
    """LF-only 文件编辑后行尾不变（旧实现 write_text 把整个文件改写成 CRLF）。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "lf.txt"
    target.write_bytes(b"a\nb\nc\n")
    try:
        result = _impl_edit(file_path=str(target), old_string="b", new_string="B")
    finally:
        _reset(tmp_path)

    assert "成功" in result
    assert target.read_bytes() == b"a\nB\nc\n"


def test_edit_crlf_file_with_lf_old_string(tmp_path):
    """CRLF 文件兼容：模型按 Read 展示的 \\n 拼 old_string 也能匹配，且行尾保持 CRLF。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "crlf.txt"
    target.write_bytes(b"a\r\nb\r\nc\r\n")
    try:
        result = _impl_edit(file_path=str(target), old_string="b\nc", new_string="B\nC")
    finally:
        _reset(tmp_path)

    assert "成功" in result
    assert target.read_bytes() == b"a\r\nB\r\nC\r\n"


def test_edit_gbk_file_roundtrip_keeps_gbk(tmp_path):
    """GBK 文件编辑：以 GBK 无损写回，而不是被 utf-8 化或产生乱码。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "gbk.txt"
    target.write_bytes("中文内容第一行\n第二行\n".encode("gbk"))
    try:
        result = _impl_edit(file_path=str(target), old_string="中文内容", new_string="中文数据")
    finally:
        _reset(tmp_path)

    assert "成功" in result
    assert target.read_bytes() == "中文数据第一行\n第二行\n".encode("gbk")


def test_edit_unencodable_content_refused_and_file_intact(tmp_path):
    """旧缺陷回归：GBK 回退读入 U+FFFD 后写回失败，但旧实现 open('w') 已把文件截成 0 字节。

    现行为：先试编码，无法无损写回时拒绝写入并返回明确错误，原文件字节保持不变。
    """
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "broken.txt"
    target.write_bytes(b"\xff\xffabc")  # UTF-8 与 GBK 严格解码均失败
    try:
        result = _impl_edit(file_path=str(target), old_string="abc", new_string="xyz")
    finally:
        _reset(tmp_path)

    assert "无法无损写回" in result
    assert target.read_bytes() == b"\xff\xffabc"


# ── ApplyPatch：空行上下文 + 行尾/编码 ─────────────────────────────────────


def test_apply_patch_blank_context_line_keeps_alignment(tmp_path):
    """统一 diff 的空上下文行（单个空格）被解析器丢弃会导致 hunk 错位损坏文件（回归测试）。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "t.txt"
    target.write_bytes(b"a\n\nc\n")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: t.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " a\n"
        " \n"  # 空上下文行：strip 后为空，旧解析器会丢弃
        "-c\n"
        "+C\n"
        "*** End Patch\n"
    )
    try:
        result = _impl_apply_patch(patch=patch)
    finally:
        _reset(tmp_path)

    assert "Updated" in result
    assert target.read_bytes() == b"a\n\nC\n"


def test_apply_patch_empty_line_as_context_keeps_alignment(tmp_path):
    """模型偷懒用纯空行表示空上下文时同样保留，不再错位删除中间行。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "t.txt"
    target.write_bytes(b"a\n\nc\n")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: t.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " a\n"
        "\n"  # 纯空行的空上下文
        "-c\n"
        "+C\n"
        "*** End Patch\n"
    )
    try:
        result = _impl_apply_patch(patch=patch)
    finally:
        _reset(tmp_path)

    assert "Updated" in result
    assert target.read_bytes() == b"a\n\nC\n"


def test_apply_patch_mismatched_context_aborts_without_write(tmp_path):
    """fail-closed：上下文行与原文不一致时中止且不写盘，绝不损坏目标文件。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "t.txt"
    target.write_bytes(b"a\nb\n")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: t.txt\n"
        "@@ -1,2 +1,2 @@\n"
        " wrong-context\n"
        "+X\n"
        "*** End Patch\n"
    )
    try:
        result = _impl_apply_patch(patch=patch)
    finally:
        _reset(tmp_path)

    assert "Update failed" in result
    assert "不一致" in result
    assert target.read_bytes() == b"a\nb\n"


def test_apply_patch_update_preserves_lf_line_endings(tmp_path):
    """LF 文件经补丁更新后行尾仍是 LF（旧实现 write_text 会整体转成 CRLF）。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "t.txt"
    target.write_bytes(b"one\ntwo\n")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: t.txt\n"
        "@@ -1 +1 @@\n"
        "-one\n"
        "+ONE\n"
        "*** End Patch\n"
    )
    try:
        result = _impl_apply_patch(patch=patch)
    finally:
        _reset(tmp_path)

    assert "Updated" in result
    assert target.read_bytes() == b"ONE\ntwo\n"


def test_apply_patch_update_gbk_file_lossless(tmp_path):
    """GBK 文件补丁更新：无损解码并以 GBK 写回，不再被 utf-8 errors=replace 永久损坏。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "gbk.txt"
    target.write_bytes("第一行\n第二行\n".encode("gbk"))
    patch = (
        "*** Begin Patch\n"
        "*** Update File: gbk.txt\n"
        "@@ -1 +1 @@\n"
        "-第一行\n"
        "+首行\n"
        "*** End Patch\n"
    )
    try:
        result = _impl_apply_patch(patch=patch)
    finally:
        _reset(tmp_path)

    assert "Updated" in result
    assert target.read_bytes() == "首行\n第二行\n".encode("gbk")


def test_apply_patch_rejects_undecodable_file_without_write(tmp_path):
    """非 UTF-8/GBK 文件：拒绝执行并保持原文件不变（不乱码化、不截断）。"""
    ws = _bind_workspace(tmp_path)
    target = ws.workspace_dir / "blob.txt"
    target.write_bytes(b"\xff\xfe\xfd\x00\x01")
    patch = (
        "*** Begin Patch\n"
        "*** Update File: blob.txt\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
        "*** End Patch\n"
    )
    try:
        result = _impl_apply_patch(patch=patch)
    finally:
        _reset(tmp_path)

    assert "Update failed" in result
    assert target.read_bytes() == b"\xff\xfe\xfd\x00\x01"
