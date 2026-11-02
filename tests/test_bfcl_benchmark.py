"""BFCL reports must not turn infrastructure failures into correct answers."""

import json

import pytest

from tools.bench_bfcl import load_categories, rescore_report, score_result
from tools import bench_bfcl


def _function(kind="string", *, required=True):
    return [{"name": "f", "parameters": {
        "type": "dict", "properties": {"x": {"type": kind}},
        "required": ["x"] if required else [],
    }}]


@pytest.mark.parametrize("actual,expected", [
    ("April 1, 2024", "april1/2024"),
    ("A-_.*/^ B", "ab"),
    ("'Quoted'", '"quoted"'),
])
def test_python_compat_uses_pinned_string_normalization(actual, expected):
    gt = [{"f": {"x": [expected]}}]
    calls = [{"name": "f", "arguments": {"x": actual}}]
    assert score_result("simple", gt, calls, scoring="python-ast-compat", functions=_function())[0]
    assert not score_result("simple", gt, calls, scoring="simplified")[0]


@pytest.mark.parametrize("kind,expected,arguments,required", [
    ("integer", [1], {"x": 1, "extra": 2}, True),
    ("integer", [1], {"x": 1.0}, True),
    ("string", [""], {}, True),
    ("dict", [{"a": [1]}], {"x": {"a": 1, "extra": 2}}, True),
    ("float", [1.0], {"x": 1.0000001}, True),
])
def test_python_compat_rejects_cases_simplified_checker_accepts(kind, expected, arguments, required):
    gt = [{"f": {"x": expected}}]
    calls = [{"name": "f", "arguments": arguments}]
    assert score_result("simple", gt, calls, scoring="simplified")[0]
    passed, reason = score_result("simple", gt, calls, scoring="python-ast-compat", functions=_function(kind, required=required))
    assert not passed
    assert reason


@pytest.mark.parametrize("kind,expected,arguments,required,want", [
    ("float", [1.0], {"x": 1}, True, True),
    ("integer", [1], {"x": True}, True, False),
    ("string", [""], {}, False, True),
    ("string", ["a\tb"], {"x": "ab"}, True, False),
    ("string", ["a:b"], {"x": "ab"}, True, False),
    ("integer", ["VARIABLE"], {"x": "variable"}, True, False),
    ("integer", ["VARIABLE"], {"x": "VARIABLE"}, True, True),
])
def test_python_compat_type_and_normalization_boundaries(kind, expected, arguments, required, want):
    assert score_result("simple", [{"f": {"x": expected}}], [{"name": "f", "arguments": arguments}],
                        scoring="python-ast-compat", functions=_function(kind, required=required))[0] is want


@pytest.mark.parametrize("actual,want", [(["A-B", "c"], True), (["c", "ab"], False), (["ab"], False), ([1, "c"], False)])
def test_python_compat_lists_are_typed_ordered_and_normalized(actual, want):
    functions = _function("array")
    functions[0]["parameters"]["properties"]["x"]["items"] = {"type": "string"}
    assert score_result("simple", [{"f": {"x": [["ab", "c"]]}}], [{"name": "f", "arguments": {"x": actual}}], scoring="python-ast-compat", functions=functions)[0] is want


@pytest.mark.parametrize("category,calls,want", [
    ("parallel", [{"name": "f", "arguments": {"x": "b"}}, {"name": "f", "arguments": {"x": "a"}}], True),
    ("parallel", [{"name": "f", "arguments": {"x": "a"}}, {"name": "f", "arguments": {"x": "a"}}], False),
    ("multiple", [{"name": "f", "arguments": {"x": "a"}}, {"name": "f", "arguments": {"x": "b"}}], False),
])
def test_python_compat_call_set_and_category_boundaries(category, calls, want):
    gt = [{"f": {"x": ["a"]}}, {"f": {"x": ["b"]}}]
    assert score_result(category, gt, calls, scoring="python-ast-compat", functions=_function())[0] is want


@pytest.mark.parametrize("actual,want", [
    ([{"a": "A-B"}], True),
    ([{"a": "ab", "extra": 1}], False),
    ([{}], False),
    ([], False),
])
def test_python_compat_dictionary_arrays(actual, want):
    functions = _function("array")
    functions[0]["parameters"]["properties"]["x"]["items"] = {"type": "dict"}
    gt = [{"f": {"x": [[{"a": ["ab"]}]]}}]
    assert score_result("simple", gt, [{"name": "f", "arguments": {"x": actual}}], scoring="python-ast-compat", functions=functions)[0] is want


@pytest.mark.parametrize("outer", ["array", "tuple"])
@pytest.mark.parametrize("inner,expected,actual,want", [
    ("float", [1.0, 2.0], [1, 2], False),
    ("float", [1, 2], [1, 2], True),
    ("float", [1.0, 2.0], [1.0, 2.0], True),
    ("integer", [1, 2], [1.0, 2.0], False),
    ("integer", [1, 2], [True, 2], False),
])
def test_python_compat_numeric_sequence_elements_follow_pinned_nested_type_check(outer, inner, expected, actual, want):
    functions = _function(outer)
    functions[0]["parameters"]["properties"]["x"]["items"] = {"type": inner}
    assert score_result("simple", [{"f": {"x": [expected]}}], [{"name": "f", "arguments": {"x": actual}}],
                        scoring="python-ast-compat", functions=functions)[0] is want


def test_python_compat_unsupported_positional_items_schema_fails_closed():
    functions = _function("array")
    functions[0]["parameters"]["properties"]["x"]["items"] = [{"type": "integer"}]
    passed, reason = score_result("simple", [{"f": {"x": [[1]]}}], [{"name": "f", "arguments": {"x": [1]}}], scoring="python-ast-compat", functions=functions)
    assert not passed
    assert reason


@pytest.mark.parametrize("keyword", ["additionalProperties", "$defs", "definitions", "anyOf", "oneOf", "allOf"])
def test_schema_adapter_normalizes_nested_schema_positions_without_rewriting_data(keyword):
    nested = {"type": "dict", "properties": {"n": {"type": "float"}}}
    value = ({"Entry": nested} if keyword in {"$defs", "definitions"}
             else [nested] if keyword in {"anyOf", "oneOf", "allOf"} else nested)
    schema = {"type": "dict", keyword: value, "default": {"type": "dict"}}
    normalized = bench_bfcl.normalize_schema(schema)
    target = normalized[keyword]
    target = (target["Entry"] if keyword in {"$defs", "definitions"}
              else target[0] if isinstance(target, list) else target)
    assert target["type"] == "object"
    assert target["properties"]["n"]["type"] == "number"
    assert normalized["default"] == {"type": "dict"}
    assert nested["type"] == "dict"


def test_python_compat_requires_schema_instead_of_silently_using_simplified():
    passed, reason = score_result("simple", [{"f": {"x": [1]}}], [{"name": "f", "arguments": {"x": 1}}], scoring="python-ast-compat")
    assert not passed
    assert "schema" in reason


def test_offline_rescore_records_mode_and_keeps_tool_loading(tmp_path):
    case = {"id": "simple_0", "function": _function(), "question": []}
    (tmp_path / "BFCL_v3_simple.json").write_text(json.dumps(case), encoding="utf-8")
    (tmp_path / "ans_BFCL_v3_simple.json").write_text(json.dumps({"id": "simple_0", "ground_truth": [{"f": {"x": ["a-b"]}}]}), encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_text(json.dumps([{"id": "simple_0", "category": "simple", "tool_calls": [{"name": "f", "arguments": {"x": "ab"}}], "passed": False, "tool_loading": "catalog"}]), encoding="utf-8")
    rows = rescore_report(report, tmp_path, scoring="python-ast-compat")
    assert rows[0]["passed"] is True
    assert rows[0]["scoring"] == "python-ast-compat"
    assert rows[0]["checker_revision"] == "58f57e9124ea981403792dd51e00a6577e621fae"
    assert rows[0]["tool_loading"] == "catalog"
    assert json.loads(report.read_text(encoding="utf-8"))[0]["passed"] is False


@pytest.mark.parametrize("options", [["--offset", "-1"], ["--limit", "-1"], ["--tool-loading", "progressive"], ["--scoring", "official"]])
def test_cli_rejects_invalid_options_before_model_or_data_access(options):
    with pytest.raises(SystemExit) as exc:
        bench_bfcl.main(["--data-dir", "not-read", *options])
    assert exc.value.code == 2


def test_cli_offset_and_loading_are_independent_of_scoring(monkeypatch):
    monkeypatch.setattr(bench_bfcl, "BASE", "unused")
    monkeypatch.setattr(bench_bfcl, "KEY", "unused")
    monkeypatch.setattr(bench_bfcl, "load_categories", lambda *_: {"simple": [{"id": str(i)} for i in range(5)]})
    seen = []
    def run(category, case, *, scoring, tool_loading):
        seen.append((case["id"], scoring, tool_loading))
        return {"id": case["id"], "passed": True, "elapsed_s": 0}
    monkeypatch.setattr(bench_bfcl, "run_case", run)
    monkeypatch.setattr(bench_bfcl, "_print_summary", lambda *_: None)
    assert bench_bfcl.main(["--data-dir", "unused", "--categories", "simple", "--offset", "2", "--limit", "2", "--tool-loading", "catalog", "--scoring", "simplified"]) == 0
    assert seen == [("2", "simplified", "catalog"), ("3", "simplified", "catalog")]


@pytest.mark.parametrize("mode", ["eager", "catalog"])
def test_run_case_passes_loading_config_and_records_modes(monkeypatch, mode):
    class LocalAgent:
        def __init__(self, **kwargs):
            assert kwargs["tool_loading"].mode == mode
        def run(self, text):
            return None
    monkeypatch.setattr(bench_bfcl, "Agent", LocalAgent)
    monkeypatch.setattr(bench_bfcl, "ModelClient", lambda **_: object())
    row = bench_bfcl.run_case("irrelevance", {"id": "irrelevance_0"}, tool_loading=mode, scoring="python-ast-compat")
    assert row["passed"] is True
    assert row["tool_loading"] == mode
    assert row["scoring"] == "python-ast-compat"


def test_cli_offline_rescore_does_not_require_model_and_labels_unknown_loading(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench_bfcl, "BASE", "")
    monkeypatch.setattr(bench_bfcl, "KEY", "")
    (tmp_path / "BFCL_v3_irrelevance.json").write_text(json.dumps({"id": "irrelevance_0", "function": []}), encoding="utf-8")
    report = tmp_path / "old.json"
    out = tmp_path / "new.json"
    report.write_text(json.dumps([{"id": "irrelevance_0", "category": "irrelevance", "passed": False, "tool_calls": []}]), encoding="utf-8")
    assert bench_bfcl.main(["--data-dir", str(tmp_path), "--rescore", str(report), "--out", str(out), "--tool-loading", "catalog"]) == 0
    row = json.loads(out.read_text(encoding="utf-8"))[0]
    assert row["scoring"] == "python-ast-compat"
    assert row["tool_loading"] == "unknown"
    summary = capsys.readouterr().out
    assert "python-ast-compat" in summary
    assert "unknown" in summary


@pytest.mark.parametrize("failure", [{"error": "TimeoutError"}, {"errors": ["HTTP 401"]}])
def test_live_scoring_rejects_runtime_failure_for_irrelevance(failure):
    passed, reason = score_result("irrelevance", [], [], **failure)

    assert passed is False
    assert reason


@pytest.mark.parametrize("status", ["failed", "denied", "cancelled"])
def test_matching_call_with_unsuccessful_execution_is_not_a_pass(status):
    passed, reason = score_result(
        "simple", [{"f": {"x": ["a"]}}],
        [{"name": "f", "arguments": {"x": "a"}, "status": status}],
    )
    assert not passed
    assert status in reason


@pytest.mark.parametrize("scoring", ["simplified", "python-ast-compat"])
def test_failed_auxiliary_call_cannot_pass_irrelevance(monkeypatch, scoring):
    class LocalAgent:
        def __init__(self, **kwargs):
            self.on_event = kwargs["on_event"]

        def run(self, text):
            self.on_event({"type": "action_end", "tool_name": "GetSkill",
                           "arguments": {}, "status": "failed", "content": "Unavailable"})

    monkeypatch.setattr(bench_bfcl, "Agent", LocalAgent)
    monkeypatch.setattr(bench_bfcl, "ModelClient", lambda **_: object())
    row = bench_bfcl.run_case("irrelevance", {"id": "irrelevance_0"}, scoring=scoring)
    assert not row["passed"]
    assert "GetSkill" in row["reason"]
    assert row["auxiliary_call_results"][0]["status"] == "failed"


@pytest.mark.parametrize("scoring", ["simplified", "python-ast-compat"])
def test_rescore_preserves_failed_auxiliary_execution(tmp_path, scoring):
    (tmp_path / "BFCL_v3_irrelevance.json").write_text(
        json.dumps({"id": "irrelevance_0", "function": []}), encoding="utf-8"
    )
    report = tmp_path / "report.json"
    report.write_text(json.dumps([{
        "id": "irrelevance_0", "category": "irrelevance", "tool_calls": [],
        "auxiliary_call_results": [{"name": "GetSkill", "status": "failed"}],
    }]), encoding="utf-8")
    row = rescore_report(report, tmp_path, scoring=scoring)[0]
    assert not row["passed"]
    assert "GetSkill" in row["reason"]


def test_successful_irrelevance_remains_correct():
    assert score_result("irrelevance", [], []) == (True, "")


def test_missing_answer_is_rejected_instead_of_scoring_empty_calls(tmp_path):
    (tmp_path / "BFCL_v3_simple.json").write_text(
        json.dumps({"id": "simple_0", "question": [], "function": []}), encoding="utf-8"
    )
    (tmp_path / "ans_BFCL_v3_simple.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="simple_0"):
        load_categories(tmp_path, ["simple"])


@pytest.mark.parametrize("failure", [{"error": "TimeoutError"}, {"errors": ["HTTP 401"]}])
def test_rescore_preserves_runtime_failure_for_irrelevance(tmp_path, failure):
    (tmp_path / "BFCL_v3_irrelevance.json").write_text(
        json.dumps({"id": "irrelevance_0", "question": [], "function": []}), encoding="utf-8"
    )
    report = tmp_path / "report.json"
    report.write_text(json.dumps([{
        "id": "irrelevance_0", "category": "irrelevance", "tool_calls": [],
        "passed": False, **failure,
    }]), encoding="utf-8")

    rows = rescore_report(report, tmp_path)

    assert rows[0]["passed"] is False
    assert rows[0]["reason"]
