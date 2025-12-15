from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Any

# import engine depuis la racine
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rules_engine import (
    load_all_rules,
    load_flags_categories,
    run_engine_on_custom_lines,
)


# ============================================================
# Paths
# ============================================================

ROOT = Path(__file__).resolve().parent
TESTS_DIR = ROOT / "generated"


# ============================================================
# Helpers
# ============================================================

def _normalize_flags(flags: List[Dict[str, Any]]) -> List[str]:
    return sorted(
        f["code"]
        for f in flags
        if f.get("triggered") is True
    )


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    sys.exit(1)


# ============================================================
# Test runner
# ============================================================

def run_suite(path: Path) -> bool:
    print(f"\n=== Running test suite: {path.name} ===")

    suite = json.loads(path.read_text(encoding="utf-8"))
    rule_code = suite.get("rule_code")

    rules = load_all_rules()
    categories = load_flags_categories()

    ok = True

    for case in suite["cases"]:
        cid = case["id"]
        desc = case.get("description", "")
        lines = case["lines"]
        all_lines = case.get("all_lines", lines)

        print(f"\n[CASE] {cid} — {desc}")

        results = run_engine_on_custom_lines(
            custom_lines=lines,
            rules=rules,
            all_lines_context=all_lines,
            flags_categories=categories,
            narrative_runner=None,
        )

        # index results by KID
        got_by_kid = {
            r["KID"]: _normalize_flags(r["flags"])
            for r in results
        }

        for kid, expected_flags in case["expected_by_kid"].items():
            expected = sorted(expected_flags)
            got = got_by_kid.get(kid, [])

            if expected != got:
                ok = False
                print(f"  ❌ KID={kid}")
                print(f"     expected: {expected}")
                print(f"     got     : {got}")
            else:
                print(f"  ✅ KID={kid} → {got}")

    return ok


# ============================================================
# CLI
# ============================================================

def main() -> int:
    if not TESTS_DIR.exists():
        _fail(f"Tests directory not found: {TESTS_DIR}")

    files = sorted(TESTS_DIR.glob("*.tests.json"))
    if not files:
        _fail("No test files found in rule_tests/generated")

    all_ok = True
    for f in files:
        if not run_suite(f):
            all_ok = False

    if not all_ok:
        print("\n=== ❌ SOME TESTS FAILED ===")
        return 1

    print("\n=== ✅ ALL TESTS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
