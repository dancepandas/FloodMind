"""Fail the stable CI gate unless every upstream job succeeded."""
import json
import os
import sys


def validate_results(needs: object) -> list[str]:
    if not isinstance(needs, dict) or not needs:
        return ["NEEDS must be a non-empty object"]
    errors = []
    for name, job in needs.items():
        if not isinstance(job, dict) or job.get("result") != "success":
            errors.append(f"{name}: expected success, got {job!r}")
    return errors


def main() -> int:
    try:
        errors = validate_results(json.loads(os.environ.get("NEEDS", "")))
    except (TypeError, ValueError) as exc:
        errors = [f"Invalid NEEDS JSON: {exc}"]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("All required jobs succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
