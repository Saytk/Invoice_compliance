# rule_tests/build_test_prompt.py
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# ============================================================
# Bootstrap project root
# ============================================================

CURRENT_FILE = Path(__file__).resolve()
CLI_DIR = CURRENT_FILE.parent
TESTS_ROOT = CLI_DIR.parent
PROJECT_ROOT = TESTS_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app_utils as h  # helper unique Зя la racine

# ============================================================
# Configuration
# ============================================================

ALLOWED_RULE_CODES = ["ADM", "BB", "CMC", "DT", "VE"]
DEFAULT_RULE_CODES = ["CMC"]

PROMPT_TPL = TESTS_ROOT / "resources" / "prompts" / "generate_tests_from_rule.txt"
PROMPTS_OUT = PROJECT_ROOT / "deterministic" / "resources" / "prompts" / "out"
RULES_OUT = PROJECT_ROOT / "deterministic" / "artifacts" / "rules_out"

RENDERED_PROMPTS = TESTS_ROOT / "resources" / "prompts" / "rendered"


# ============================================================
# Internal helpers
# ============================================================

def find_rule_py(rule_code: str) -> Path:
    candidates = list(RULES_OUT.glob(f"{rule_code}__*.py"))
    if candidates:
        return candidates[0]

    for fp in RULES_OUT.glob("*.py"):
        txt = fp.read_text(encoding="utf-8", errors="ignore")
        if f'@rule("{rule_code}"' in txt or f"@rule('{rule_code}'" in txt:
            return fp

    raise RuntimeError(f"Rule file not found for {rule_code} in rules_out/")


def build_llm_test_prompt(rule_code: str) -> str:
    """
    Construit le prompt final (string) Зя envoyer au LLM,
    SANS faire d'appel OpenAI.
    """
    rule_code = rule_code.strip().upper()

    prompt_path = PROMPTS_OUT / f"final_prompt_{rule_code}.txt"
    if not prompt_path.exists():
        raise RuntimeError(f"Prompt not found: {prompt_path}")

    if not PROMPT_TPL.exists():
        raise RuntimeError(f"Missing test prompt template: {PROMPT_TPL}")

    rule_py_path = find_rule_py(rule_code)

    prompt_text = h.read_text(prompt_path)
    rule_py = h.read_text(rule_py_path)
    tpl = h.read_text(PROMPT_TPL)

    llm_prompt = h.render_placeholders(
        tpl,
        {
            "FINAL_PROMPT": prompt_text,
            "RULE_PY": rule_py,
        },
        src_name=PROMPT_TPL.name,
    )
    return llm_prompt


def write_rendered_prompt(rule_code: str, *, overwrite: bool = False) -> Path:
    """
    Ecrit le prompt final rendu dans tests/resources/prompts/rendered/<CODE>.final_prompt.txt
    et retourne le path.
    """
    rule_code = rule_code.strip().upper()
    RENDERED_PROMPTS.mkdir(parents=True, exist_ok=True)

    out_path = RENDERED_PROMPTS / f"{rule_code}.final_prompt.txt"
    if out_path.exists() and not overwrite:
        raise RuntimeError(f"Rendered prompt already exists: {out_path} (use --overwrite)")

    llm_prompt = build_llm_test_prompt(rule_code)
    out_path.write_text(llm_prompt, encoding="utf-8")
    return out_path


# ============================================================
# Main
# ============================================================

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build rendered LLM test prompts (no API calls).")
    parser.add_argument("--code", type=str, help="Rule code to build (e.g. CMC or ALL).")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if not args.code:
        rule_codes = DEFAULT_RULE_CODES
        print("[INFO] No --code provided ѓЕ' default execution:", ", ".join(rule_codes))
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
            out_path = write_rendered_prompt(rc, overwrite=True)
            print(f"[OK] Rendered prompt for {rc}")
            print(f"     ѓЕ' {out_path}")
        except Exception as e:
            print(f"[FAIL] {rc}: {e}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
