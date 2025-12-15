from __future__ import annotations

import sys
from pathlib import Path

from app_utils import (
    read_text,
    parse_paths_file,
    parse_rule_codes_file,
    default_penalty_for,
    load_flags_info,
    render_placeholders,
    make_joined_invoice_sample,
)

ROOT = Path(__file__).resolve().parent
TPL = ROOT / "prompts" / "global_prompt_minimal.txt"
OUT_DIR = ROOT / "prompts" / "out"
PH_DIR = ROOT / "prompts" / "placeholders"
PATHS_TXT = PH_DIR / "paths.txt"

SAMPLES = {
    "FLAGS_SAMPLE": PH_DIR / "FLAGS_SAMPLE.txt",
    "UTBMS_SAMPLE": PH_DIR / "UTBMS_SAMPLE.txt",
    "INVOICE_SAMPLE": PH_DIR / "INVOICE_SAMPLE.txt",
}
RULES_TXT = ROOT / "prompts" / "rules" / "current_rules.txt"


def function_name_for(code: str) -> str:
    return f"rule_{code.lower()}"


def build_prompt_for_rule(code: str) -> str:
    template = read_text(TPL)

    paths = parse_paths_file(
        PATHS_TXT,
        required={"FLAGS_PATH", "UTBMS_PATH", "INVOICE_PATH"},
    )

    base_subs = {ph: read_text(fp) for ph, fp in SAMPLES.items()}

    # join invoice sample + utbms sample
    try:
        base_subs["INVOICE_SAMPLE"] = make_joined_invoice_sample(
            base_subs.get("INVOICE_SAMPLE", ""),
            base_subs.get("UTBMS_SAMPLE", ""),
        )
    except Exception as e:
        print(f"[WARN] Could not join INVOICE_SAMPLE with UTBMS_SAMPLE: {e}", file=sys.stderr)

    base_subs.update(paths)

    info = load_flags_info(paths["FLAGS_PATH"], code)
    if info:
        _, description, penalty = info
    else:
        description = f"Rule {code}."
        penalty = default_penalty_for(code)

    subs = dict(base_subs)
    subs.update({
        "RULE_CODE": code,
        "RULE_DESCRIPTION_1_OR_2_SENTENCES": description,
        "DEFAULT_PENALTY_0_TO_1": f"{penalty:.6g}",
        "RULE_VERSION_INT": "1",
        "RULE_DESCRIPTION": description,
        "DEFAULT_PENALTY": f"{penalty:.6g}",
        "RULE_VERSION": "1",
        "FUNCTION_NAME": function_name_for(code),
    })

    return render_placeholders(template, subs, src_name=TPL.name)


def main():
    codes = parse_rule_codes_file(RULES_TXT)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for code in codes:
        prompt = build_prompt_for_rule(code)
        out_path = OUT_DIR / f"final_prompt_{code}.txt"
        out_path.write_text(prompt, encoding="utf-8")
        print(f"[OK] {out_path.relative_to(ROOT)}  (code={code})")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
