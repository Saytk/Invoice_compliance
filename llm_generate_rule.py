from __future__ import annotations

from pathlib import Path
import os
import re
import sys
import time
import argparse

from prompts_generator import build_prompt_for_rule

from app_utils import (
    parse_rule_codes_file,
    call_openai_responses_text,
)

ROOT = Path(__file__).resolve().parent
PROMPTS_OUT = ROOT / "prompts" / "out"
RULES_OUT = ROOT / "rules_out"
LOGS_FAIL = ROOT / "logs_failed"
OPENAI_KEY_FILE = ROOT / "openai_key.txt"
RULES_TXT = ROOT / "prompts" / "rules" / "current_rules.txt"

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.1")


# -----------------------
# Extraction & parsing
# -----------------------
def _largest_code_block(s: str) -> str | None:
    blocks = re.findall(r"~~~python\s*(.*?)~~~", s, re.DOTALL | re.IGNORECASE)
    if not blocks:
        blocks = re.findall(r"```python\s*(.*?)```", s, re.DOTALL | re.IGNORECASE)
    if not blocks:
        blocks = re.findall(r"~~~\s*(.*?)~~~", s, re.DOTALL)
    if not blocks:
        blocks = re.findall(r"```\s*(.*?)```", s, re.DOTALL)
    if not blocks:
        return None
    return max(blocks, key=len).strip()


def _extract_decorated_function(code: str) -> str:
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    m = re.search(r"^.*?@rule\(", code, flags=re.DOTALL | re.MULTILINE)
    if not m:
        return code.strip()
    start = m.start()
    sliced = code[start:].lstrip()
    nxt = re.search(r"\n@rule\(", sliced)
    if nxt:
        sliced = sliced[:nxt.start()].rstrip()
    return sliced.strip()


def _has_single_decorated_function(code: str) -> bool:
    deco_count = len(re.findall(r"^@rule\(", code, flags=re.MULTILINE))
    def_count = len(re.findall(r"^def\s+\w+\s*\(", code, flags=re.MULTILINE))
    return deco_count == 1 and def_count == 1


def _sanitize_code(code: str) -> str:
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    code = code.replace("~~~python", "").replace("~~~", "")
    code = code.replace("```python", "").replace("```", "")
    code = code.strip()

    code = re.sub(r"^\s*import[^\n]*\n", "", code, flags=re.MULTILINE)
    code = re.sub(r"^\s*from\s+\w+(?:\.\w+)*\s+import[^\n]*\n", "", code, flags=re.MULTILINE)

    code = re.sub(r'(@rule\(\s*"[^"]+"\s*,\s*penalty=\s*)(\d+)(\s*,)', r"\1\2.0\3", code)
    code = re.sub(r'(RuleResult\(\s*True\s*,\s*"[^"]+"\s*,\s*)(\d+)(\s*,)', r"\1\2.0\3", code)

    return code.strip()


def _detect_needed_imports(code: str) -> list[str]:
    imports: list[str] = []

    if re.search(r"\bdatetime\s*\.", code) or re.search(r"\bdatetime\s*\(", code) or re.search(r"\bdate\s*\(", code):
        imports.append("from datetime import datetime, date")

    if re.search(r"\bDecimal\s*\(", code):
        imports.append("from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN")

    if re.search(r"\bre\.", code):
        imports.append("import re")

    if re.search(r"\bCounter\b", code):
        imports.append("from collections import Counter")
    if re.search(r"\bdefaultdict\s*\(", code):
        imports.append("from collections import defaultdict")

    if re.search(r"\bmath\.", code):
        imports.append("import math")

    if re.search(r"\bitertools\.", code) or re.search(r"\bgroupby\s*\(", code) or re.search(r"\bchain\s*\(", code):
        imports.append("import itertools")

    # dedupe keep order
    seen = set()
    out = []
    for imp in imports:
        if imp not in seen:
            out.append(imp)
            seen.add(imp)
    return out


def _prepend_imports(imports: list[str], code: str) -> str:
    if not imports:
        return code
    header = "\n".join(imports).rstrip()
    return f"{header}\n\n{code.lstrip()}"


# -----------------------
# Logging
# -----------------------
def _log_failure(
    rule_code: str,
    reason: str,
    content: str,
    block: str | None = None,
    code_text: str | None = None,
    debug: bool = False,
):
    LOGS_FAIL.mkdir(parents=True, exist_ok=True)
    fp = LOGS_FAIL / f"log_fail_{rule_code}.txt"

    parts = [
        f"[FAIL] Rule {rule_code}",
        f"Reason: {reason}",
        "\n===== RAW CONTENT =====",
        (content or "").strip(),
    ]
    if block is not None:
        parts += ["\n===== LARGEST CODE BLOCK =====", block.strip()]
    if code_text is not None:
        parts += ["\n===== EXTRACTED CODE TEXT =====", code_text.strip()]

    text = "\n".join(parts) + "\n"
    fp.write_text(text, encoding="utf-8")
    print(f"[LOG] Saved failure content to {fp}")
    if debug:
        print(text)


# -----------------------
# Prompt helpers
# -----------------------
def infer_names_from_prompt(prompt_text: str) -> tuple[str, str]:
    def grab(key: str) -> str:
        m = re.search(rf"{key}\s*:\s*['\"]?(.+?)['\"]?\s*$", prompt_text, re.MULTILINE)
        if not m:
            raise RuntimeError(f"Could not infer {key} from prompt")
        return m.group(1).strip().strip("<>").strip()
    return grab("RULE_CODE"), grab("FUNCTION_NAME")


# ============================================================
# API haut niveau : génération d'une règle depuis un prompt
# ============================================================
def generate_rule_from_prompt(prompt: str, *, debug: bool = False) -> Path | None:
    rule_code, func = infer_names_from_prompt(prompt)

    print(f"[GEN] Generating rule for {rule_code} -> {func}", file=sys.stderr)

    content = call_openai_responses_text(
        prompt,
        model=MODEL,
        api_key_file=OPENAI_KEY_FILE,
        raw_log_path=None,
        timeout=120,
        retries=4,
    )

    block = _largest_code_block(content)

    if block is None:
        code_text = _extract_decorated_function(content)
        if "@rule(" not in code_text:
            _log_failure(rule_code, "No fenced code block AND '@rule(' not found in whole response", content, None, None, debug)
            return None
    else:
        code_text = _extract_decorated_function(block)
        if "@rule(" not in code_text:
            fallback = _extract_decorated_function(content)
            if "@rule(" in fallback:
                code_text = fallback
            else:
                _log_failure(rule_code, "Decorator '@rule(' not found after extraction", content, block, code_text, debug)
                return None

    code_text = _sanitize_code(code_text)
    imports = _detect_needed_imports(code_text)
    final_code = _prepend_imports(imports, code_text)

    if "@rule(" not in final_code:
        _log_failure(rule_code, "Decorator '@rule(' not present after sanitization", content, block, final_code, debug)
        return None
    if not _has_single_decorated_function(final_code):
        _log_failure(rule_code, "Expected exactly one decorated function", content, block, final_code, debug)
        return None

    RULES_OUT.mkdir(parents=True, exist_ok=True)
    target = RULES_OUT / f"{rule_code.upper()}__{func}.py"
    target.write_text(final_code.rstrip() + "\n", encoding="utf-8")
    print(f"[OK] {target.relative_to(ROOT)}", file=sys.stderr)
    return target


def generate_rule_from_code(rule_code: str, debug: bool = False) -> Path | None:
    prompt = build_prompt_for_rule(rule_code)
    inferred_code, _ = infer_names_from_prompt(prompt)
    if inferred_code.upper() != rule_code.upper():
        print(
            f"[WARN] RULE_CODE mismatch between requested ({rule_code}) and prompt ({inferred_code}). Using {inferred_code}.",
            file=sys.stderr,
        )
    return generate_rule_from_prompt(prompt, debug=debug)


# ============================================================
# Programme batch
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Generate rule functions from prompts.")
    parser.add_argument("--debug", action="store_true", help="Print raw response and extraction info on failures.")
    parser.add_argument("--from-codes", action="store_true", help="Generate from current_rules.txt codes instead of prompts/out files.")
    args = parser.parse_args()
    debug = args.debug

    RULES_OUT.mkdir(parents=True, exist_ok=True)

    if args.from_codes:
        codes = parse_rule_codes_file(RULES_TXT)
        for c in codes:
            generate_rule_from_code(c, debug=debug)
        print("[DONE] All rules generated.")
        return

    wanted_codes = set(parse_rule_codes_file(RULES_TXT))

    files = sorted(PROMPTS_OUT.glob("final_prompt_*.txt"))
    if not files:
        raise SystemExit("[ERR] No prompts found in prompts/out/")

    for p in files:
        prompt = p.read_text(encoding="utf-8")
        rule_code, _ = infer_names_from_prompt(prompt)
        if rule_code not in wanted_codes:
            print(f"[SKIP] {rule_code} (not in {RULES_TXT.name})")
            continue
        generate_rule_from_prompt(prompt, debug=debug)

    print("[DONE] All rules generated.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
