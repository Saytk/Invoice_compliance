# rule_tests/generate_rule_tests.py
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ============================================================
# Bootstrap project root
# ============================================================

ROOT = Path(__file__).resolve().parent          # .../rule_tests
PROJECT_ROOT = ROOT.parent                      # racine projet
sys.path.insert(0, str(PROJECT_ROOT))

import app_utils as h  # helper unique à la racine
from build_test_prompt import (
    ALLOWED_RULE_CODES,
    DEFAULT_RULE_CODES,
    write_rendered_prompt,
)

# ============================================================
# Configuration
# ============================================================

TESTS_OUT = ROOT / "generated"
RAW_LOGS = ROOT / "raw_llm_logs"

DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.1")
OPENAI_KEY_FILE = PROJECT_ROOT / "openai_key.txt"


def _looks_like_csv_tests(txt: str) -> bool:
    """
    Heuristique minimale (non bloquante) :
    - doit contenir un header avec '...KID,LABEL'
    - doit contenir au moins un marker ###TEST:
    """
    t = (txt or "").strip()
    if not t:
        return False
    if "###TEST:" not in t:
        return False
    # on tolère des espaces/retours à la ligne, mais on veut voir KID et LABEL dans la 1ère ligne
    first_line = t.splitlines()[0].strip()
    if "KID" not in first_line or "LABEL" not in first_line:
        return False
    return True


def generate_tests_for_rule(rule_code: str, *, overwrite: bool, model: str) -> None:
    rule_code = rule_code.strip().upper()

    # 1) Build prompt (and write it for debug)
    rendered_prompt_path = write_rendered_prompt(rule_code, overwrite=True)
    llm_prompt = h.read_text(rendered_prompt_path)

    # 2) Output paths
    TESTS_OUT.mkdir(parents=True, exist_ok=True)
    RAW_LOGS.mkdir(parents=True, exist_ok=True)

    out_path = TESTS_OUT / f"{rule_code}.tests.csv"
    raw_log_path = RAW_LOGS / f"raw_{rule_code}.json"

    if out_path.exists() and not overwrite:
        raise RuntimeError(f"Test file already exists: {out_path} (use --overwrite)")

    # 3) Call OpenAI (CSV text expected)
    csv_text = h.call_openai_responses_text(
        llm_prompt,
        model=model,
        api_key_file=OPENAI_KEY_FILE if OPENAI_KEY_FILE.exists() else None,
        raw_log_path=raw_log_path,
        timeout=180,
        retries=4,
    )

    # 4) Light validation (warning only, but we still write the output)
    if not _looks_like_csv_tests(csv_text):
        # On n'échoue pas : on écrit quand même pour debug, mais on signale
        print(f"[WARN] Output for {rule_code} does not look like expected CSV blocks. Writing anyway...")

    # 5) Write CSV as-is
    out_path.write_text(csv_text.rstrip() + "\n", encoding="utf-8")

    print(f"[OK] Generated tests for {rule_code}")
    print(f"     → tests: {out_path}")
    print(f"     → raw log: {raw_log_path}")
    print(f"     → rendered prompt: {rendered_prompt_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate LLM-based CSV test suites for rules.")
    parser.add_argument("--code", type=str, help="Rule code to generate (e.g. CMC or ALL).")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    args = parser.parse_args()

    if not args.code:
        rule_codes = DEFAULT_RULE_CODES
        print("[INFO] No --code provided → default execution:", ", ".join(rule_codes))
    elif args.code.upper() == "ALL":
        rule_codes = ALLOWED_RULE_CODES
    else:
        rc = args.code.strip().upper()
        if rc not in ALLOWED_RULE_CODES:
            print("[ERR] Invalid rule code:", rc)
            print("Allowed:", ", ".join(ALLOWED_RULE_CODES))
            return 1
        rule_codes = [rc]

    for rc in rule_codes:
        try:
            generate_tests_for_rule(rc, overwrite=args.overwrite, model=args.model)
        except Exception as e:
            print(f"[FAIL] {rc}: {e}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
