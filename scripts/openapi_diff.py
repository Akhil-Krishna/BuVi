"""Compare generated OpenAPI contracts with the committed ones (Section 26).

Fails on any drift. A breaking change (removed operation, new required parameter or
required body, removed success response) additionally needs a major `info.version`
bump -- Section 26: "fail on breaking change without version bump".

An operation the committed contract marks `x-available-in-phase` is a `501` stub for a
backend that did not exist yet: it promised nothing, so replacing it with the real
contract is not a breaking change (ADR 0004).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

METHODS = {"get", "post", "put", "patch", "delete"}


def _ops(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, method): op
        for path, item in doc.get("paths", {}).items()
        for method, op in item.items()
        if method in METHODS
    }


def breaking_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    old_ops, new_ops = _ops(old), _ops(new)
    for key, old_op in old_ops.items():
        new_op = new_ops.get(key)
        label = f"{key[1].upper()} {key[0]}"
        if old_op.get("x-available-in-phase") and new_op is not None:
            continue
        if new_op is None:
            problems.append(f"removed operation {label}")
            continue
        old_required = {
            (p["in"], p["name"]) for p in old_op.get("parameters", []) if p.get("required")
        }
        for param in new_op.get("parameters", []):
            if param.get("required") and (param["in"], param["name"]) not in old_required:
                problems.append(f"{label}: new required parameter {param['name']}")
        if new_op.get("requestBody", {}).get("required") and not old_op.get("requestBody", {}).get(
            "required"
        ):
            problems.append(f"{label}: request body became required")
        old_ok = {c for c in old_op.get("responses", {}) if c.startswith("2")}
        new_ok = {c for c in new_op.get("responses", {}) if c.startswith("2")}
        for code in sorted(old_ok - new_ok):
            problems.append(f"{label}: removed success response {code}")
    return problems


def _major(doc: dict[str, Any]) -> str:
    return str(doc.get("info", {}).get("version", "0")).split(".")[0]


def main() -> int:
    committed_dir, generated_dir = Path(sys.argv[1]), Path(sys.argv[2])
    failed = False
    for generated in sorted(generated_dir.glob("*.json")):
        committed = committed_dir / generated.name
        new = json.loads(generated.read_text())
        if not committed.is_file():
            print(f"FAIL {generated.name}: contract not committed (run `make contracts`)")
            failed = True
            continue
        old = json.loads(committed.read_text())
        if old == new:
            print(f"ok   {generated.name}")
            continue
        failed = True
        breaking = breaking_changes(old, new)
        if breaking and _major(old) == _major(new):
            print(f"FAIL {generated.name}: breaking change without a major version bump")
            for problem in breaking:
                print(f"       - {problem}")
        else:
            print(
                f"FAIL {generated.name}: committed contract is out of date (run `make contracts`)"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
