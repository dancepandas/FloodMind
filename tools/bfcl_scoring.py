"""Independent Python JSON-call subset of the pinned BFCL AST checker.

Not the official evaluator: no AST parsing, model-name conversion, Java/JS,
execution checks or leaderboard aggregation. Unsupported schemas fail closed.
Reference: ShishirPatil/gorilla, bfcl_eval/eval_checker/ast_eval/ast_checker.py
at CHECKER_REVISION. Only the source was read; no upstream code is executed.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

CHECKER_REVISION = "58f57e9124ea981403792dd51e00a6577e621fae"
SCORING_MODES = ("simplified", "python-ast-compat")
_TYPES = {"string": str, "integer": int, "float": float, "boolean": bool,
          "array": list, "tuple": list, "dict": dict, "any": str}


def _standardize(value: Any) -> Any:
    if type(value) is str:
        return re.sub(r"[ ,./\-_*^]", "", value).lower().replace("'", '"')
    return value


def _answer_type(possible: list) -> type | None:
    return next((type(answer) for answer in possible if answer != ""), None)


def _typed(value: Any, possible: list, expected: type) -> bool:
    # Pinned checker permits variable representations typed like the answer.
    return type(value) is expected or type(value) is _answer_type(possible)


def _dict_matches(value: dict, candidate: Any) -> bool:
    if not isinstance(candidate, dict) or set(value) - set(candidate):
        return False
    for key, possible in candidate.items():
        if not isinstance(possible, list):
            return False
        if key not in value:
            if "" not in possible:
                return False
        elif _standardize(value[key]) not in [_standardize(item) for item in possible]:
            return False
    return True


def _value_matches(value: Any, possible: list, spec: dict) -> bool:
    kind = spec.get("type")
    if not isinstance(kind, str) or kind not in _TYPES:
        return False
    expected = _TYPES[kind]
    if kind == "float" and type(value) is int:
        value = float(value)
    if not _typed(value, possible, expected):
        return False
    answer_type = _answer_type(possible)
    variable = answer_type is not None and answer_type is not expected
    if variable:
        return value in possible  # variables are case-sensitive, no normalization
    if expected is str:
        return _standardize(value) in [_standardize(item) for item in possible if type(item) is str]
    if expected is dict:
        return any(_dict_matches(value, candidate) for candidate in possible)
    if expected is list:
        items = spec.get("items", {})
        if not isinstance(items, dict):
            return False
        nested_kind = items.get("type")
        if not isinstance(nested_kind, str) or nested_kind not in _TYPES:
            return False
        inner = _TYPES[nested_kind]
        # One level of nested type checks, as in the pinned checker.
        if not any(isinstance(candidate, list) and all(_typed(item, candidate, inner) for item in value)
                   for candidate in possible):
            return False
        if inner is dict:
            return any(isinstance(candidate, list) and len(candidate) == len(value)
                       and all(_dict_matches(item, answer) for item, answer in zip(value, candidate))
                       for candidate in possible)
        normalized = [_standardize(item) for item in value]
        return any(isinstance(candidate, list) and normalized == [_standardize(item) for item in candidate]
                   for candidate in possible)
    return value in possible  # exact numeric equality, not the simplified tolerance


def _call_matches(gt: dict, call: dict, functions: Sequence[dict]) -> str | None:
    if len(gt) != 1:
        return "invalid ground truth call"
    name, answers = next(iter(gt.items()))
    if call.get("name") != name:
        return "function name mismatch"
    function = next((item for item in functions if item.get("name") == name), None)
    if function is None:
        return f"missing function schema: {name}"
    schema = function.get("parameters", {})
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        return f"unsupported function schema: {name}"
    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    for key in required:
        if key not in arguments:
            return f"missing required parameter: {key}"
    for key, value in arguments.items():
        if key not in properties or key not in answers:
            return f"unexpected parameter: {key}"
        possible = answers[key]
        if not isinstance(possible, list) or not _value_matches(value, possible, properties[key]):
            return f"parameter type/value mismatch or unsupported schema: {key}"
    for key, possible in answers.items():
        if key not in arguments and (not isinstance(possible, list) or "" not in possible):
            return f"missing parameter: {key}"
    return None


def score_python_compat(category: str, ground_truth: list, calls: list,
                        functions: Sequence[dict]) -> tuple[bool, str]:
    """Score supported Python JSON calls; greedy unordered pairing matches reference."""
    if category == "irrelevance":
        return (False, "irrelevance must not call tools") if calls else (True, "")
    if not functions:
        return False, "missing function schema for python-ast-compat"
    if len(calls) != len(ground_truth):
        return False, "wrong number of function calls"
    if category in ("simple", "multiple") and len(ground_truth) != 1:
        return False, "unsupported simple/multiple call count (expected one)"
    remaining = list(calls)
    for gt in ground_truth:
        reasons = []
        for index, call in enumerate(remaining):
            reason = _call_matches(gt, call, functions)
            if reason is None:
                remaining.pop(index)
                break
            reasons.append(reason)
        else:
            return False, "; ".join(reasons) or "no matching call"
    return True, ""
