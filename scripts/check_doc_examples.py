"""Compile and execute only explicitly registered contract examples."""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import tempfile

EXAMPLES = {"tool-basic": "docs/development/adding-a-tool.md"}


def extract_examples(text: str) -> dict[str, str]:
    examples = {}
    lines = text.splitlines(keepends=True)
    fence = None
    marker = None
    body = []
    for line in lines:
        stripped = line.strip()
        if fence is None:
            opening = re.fullmatch(r"(`{3,}|~{3,})(.*)", stripped)
            if not opening:
                continue
            fence, info = opening.groups()
            if "contract-example" in info:
                match = re.fullmatch(r"python contract-example=([a-z][a-z0-9-]*)", info)
                if not match:
                    raise ValueError(f"invalid contract marker: {info}")
                marker = match[1]
                if marker in examples:
                    raise ValueError(f"duplicate marker: {marker}")
            body = []
        elif re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + r",}", stripped):
            if marker is not None:
                examples[marker] = "".join(body)
            fence = marker = None
        else:
            body.append(line)
    if marker is not None:
        raise ValueError(f"unclosed block: {marker}")
    return examples


def load_examples(root: Path) -> dict[str, str]:
    result = {}
    for relative in sorted(set(EXAMPLES.values())):
        try:
            extracted = extract_examples((root / relative).read_text(encoding="utf-8"))
            expected = {key for key, path in EXAMPLES.items() if path == relative}
            if set(extracted) != expected:
                raise ValueError(f"expected {sorted(expected)}, found {sorted(extracted)}")
            for marker, code in extracted.items():
                compile(code, f"{relative}:{marker}", "exec")
            result.update(extracted)
        except (OSError, ValueError, SyntaxError) as exc:
            raise ValueError(f"{relative}: {exc}") from exc
    return result


def main(root: Path | None = None) -> int:
    root = root or Path(__file__).resolve().parents[1]
    try:
        examples = load_examples(root)
        original = Path.cwd()
        with tempfile.TemporaryDirectory(prefix="floodmind-docs-") as temporary:
            try:
                os.chdir(temporary)
                for marker, code in examples.items():
                    exec(compile(code, f"{EXAMPLES[marker]}:{marker}", "exec"), {"__name__": "__contract_example__"})
            finally:
                os.chdir(original)
        print(f"Contract examples passed: {len(examples)}")
        return 0
    except Exception as exc:
        print(f"Contract example check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
