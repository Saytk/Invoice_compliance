from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


# ============================================================
# Paths (aligned with your architecture)
# ============================================================
CURRENT_FILE = Path(__file__).resolve()
CORE_DIR = CURRENT_FILE.parent
NARRATIVE_ROOT = CORE_DIR.parent
PROJECT_ROOT = CORE_DIR.parents[1]

# Sample invoices for prompt tests
ARTIFACTS_DIR = NARRATIVE_ROOT / "artifacts"
DEFAULT_INVOICES_CSV = ARTIFACTS_DIR / "sample_input" / "invoices_to_check.csv"

# Required fields per flag (narrative/resources)
DEFAULT_REQUIRED_FIELDS_JSON = NARRATIVE_ROOT / "resources" / "required_fields.json"

# Flags master file at project root: data/flags.csv
DEFAULT_FLAGS_CSV = PROJECT_ROOT / "data" / "flags.csv"

# Prompt template
PROMPT_TPL = NARRATIVE_ROOT / "resources" / "prompts" / "narrative_batch_csv.txt"

# Rendered output folder
DEFAULT_RENDERED_DIR = NARRATIVE_ROOT / "resources" / "prompts" / "rendered"


# ============================================================
# Utilities
# ============================================================
def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        x = (x or "").strip()
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _clean_flag_codes(flag_codes: List[str]) -> List[str]:
    return _dedupe_keep_order(
        [(c or "").strip().upper() for c in (flag_codes or []) if c and c.strip()]
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


# ============================================================
# Loaders
# ============================================================
def _load_required_fields_map(path: Path) -> Dict[str, List[str]]:
    """
    Input: narrative/resources/required_fields.json
    Output: dict { FLAG_CODE: [fields...] }
    """
    data = _read_json(path)
    out: Dict[str, List[str]] = {}
    if not isinstance(data, dict):
        return out

    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, list):
            continue
        code = k.strip().upper()
        if not code:
            continue
        out[code] = [f.strip() for f in v if isinstance(f, str) and f.strip()]
    return out


def _load_lines_from_invoices_csv(path: Path) -> List[Dict[str, Any]]:
    """
    Reads invoices_to_check.csv into list[dict].
    Keeps the original invoice CSV headers as keys.
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing invoices CSV: {path}")

    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _load_flags_desc_from_flags_csv(path: Path) -> Dict[str, str]:
    """
    Reads PROJECT_ROOT/data/flags.csv and returns mapping:
      FLAG_ABBR -> "<FLAG NAME> | <FLAG DESCRIPTION>"
    Expected headers:
      GROUP NAME,FLAG ABBR,FLAG NAME,FLAG DESCRIPTION,FLAG PENALTY__
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing flags CSV: {path}")

    out: Dict[str, str] = {}

    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not isinstance(row, dict):
                continue

            abbr = (row.get("FLAG ABBR") or "").strip().upper()
            if not abbr:
                continue

            name = (row.get("FLAG NAME") or "").strip()
            desc = (row.get("FLAG DESCRIPTION") or "").strip()

            if name and desc:
                text = f"{name} | {desc}"
            else:
                text = name or desc or ""

            out[abbr] = text

    return out


# ============================================================
# Union logic (per selected flags)
# ============================================================
def _union_headers_for_flags(
    *,
    flag_codes: List[str],
    required_fields_map: Dict[str, List[str]],
    always_first: List[str] | None = None,
) -> List[str]:
    """
    Build union headers = union(required fields per flag), ignoring ALL_LINES.
    Always includes fields in always_first (default: ["KID"]) at the beginning.
    """
    always_first = always_first or ["KID"]
    union: List[str] = []

    for code in flag_codes:
        fields = required_fields_map.get(code, []) or []
        for f in fields:
            if not isinstance(f, str):
                continue
            f = f.strip()
            if not f:
                continue
            if f.upper() == "ALL_LINES":
                continue
            union.append(f)

    return _dedupe_keep_order(always_first + union)


def _lines_to_union_csv_text(
    *,
    lines: List[Dict[str, Any]],
    union_headers: List[str],
) -> str:
    """
    Produce CSV text with header=union_headers.
    Each row fills only available keys; others remain empty.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=union_headers,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()

    for ln in (lines or []):
        if not isinstance(ln, dict):
            continue
        row = {}
        for h in union_headers:
            v = ln.get(h, "")
            if v is None:
                v = ""
            row[h] = v
        writer.writerow(row)

    return buf.getvalue().strip()


# ============================================================
# Core builder (inputs for prompt)
# ============================================================
def build_prompt_inputs(
    *,
    lines: List[Dict[str, Any]],
    flag_codes: List[str],
    flags_desc: Dict[str, str],
    required_fields_map: Dict[str, List[str]],
) -> Dict[str, Any]:
    """
    Builds the assets you want:
    - FLAGS_BLOCK: "- CODE: description" lines
    - UNION_HEADERS: list
    - UNION_HEADERS_CSV: comma-separated header line
    - INVOICES_CSV_UNION: CSV text with header=union and lines formatted in that schema
    """
    flags = _clean_flag_codes(flag_codes)

    union_headers = _union_headers_for_flags(
        flag_codes=flags,
        required_fields_map=required_fields_map,
        always_first=["KID"],
    )

    invoices_csv_union = _lines_to_union_csv_text(
        lines=lines,
        union_headers=union_headers,
    )

    flags_json: Dict[str, str] = {c: (flags_desc.get(c, "") or "").strip() for c in flags}
    flags_block = "\n".join(f"- {c}: {flags_json.get(c, '')}" for c in flags).strip()

    return {
        "FLAG_CODES": flags,
        "FLAGS_BLOCK": flags_block,
        "UNION_HEADERS": union_headers,
        "UNION_HEADERS_CSV": ",".join(union_headers),
        "INVOICES_CSV_UNION": invoices_csv_union,
    }


# ============================================================
# Prompt renderer (creates final prompt file)
# ============================================================
def render_narrative_prompt(
    *,
    subs: Dict[str, Any],
    template_path: Path,
) -> Tuple[str, Dict[str, Any]]:
    """
    Replaces <<PLACEHOLDERS>> in the template with values from subs.
    Returns (prompt_text, meta).
    """
    if not template_path.exists():
        raise FileNotFoundError(f"Missing prompt template: {template_path}")

    template = template_path.read_text(encoding="utf-8", errors="replace")
    prompt = template

    for k, v in subs.items():
        key = f"<<{k}>>"
        if isinstance(v, (dict, list)):
            # For this use-case we prefer text, but keep JSON rendering safe for debug keys if ever added.
            sval = json.dumps(v, indent=2, ensure_ascii=False)
        else:
            sval = "" if v is None else str(v)
        prompt = prompt.replace(key, sval)

    meta = {
        "template": template_path.as_posix(),
        "n_flags": len(subs.get("FLAG_CODES", []) or []),
        "n_union_headers": len(subs.get("UNION_HEADERS", []) or []),
        "csv_chars": len(subs.get("INVOICES_CSV_UNION", "") or ""),
    }
    return prompt, meta


# ============================================================
# CLI (defaults use artifacts/sample_input/invoices_to_check.csv)
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and render the final narrative prompt (defaults use artifacts sample invoices CSV)."
    )
    parser.add_argument("--invoices-csv", type=str, default=str(DEFAULT_INVOICES_CSV))
    parser.add_argument("--required-fields-json", type=str, default=str(DEFAULT_REQUIRED_FIELDS_JSON))
    parser.add_argument("--flags-csv", type=str, default=str(DEFAULT_FLAGS_CSV))
    parser.add_argument("--template", type=str, default=str(PROMPT_TPL))
    parser.add_argument(
        "--flag-codes",
        type=str,
        default="",
        help="Comma-separated flag codes (default: all keys from required_fields.json)",
    )
    parser.add_argument("--prefix", type=str, default="DEFAULT")
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Write final prompt to this file (default: narrative/resources/prompts/rendered/narrative_prompt_<prefix>.txt)",
    )

    args = parser.parse_args()

    required_fields_map = _load_required_fields_map(Path(args.required_fields_json))
    flags_desc = _load_flags_desc_from_flags_csv(Path(args.flags_csv))
    lines = _load_lines_from_invoices_csv(Path(args.invoices_csv))

    if args.flag_codes.strip():
        flag_codes = _clean_flag_codes([x.strip() for x in args.flag_codes.split(",") if x.strip()])
    else:
        flag_codes = sorted(required_fields_map.keys())

    subs = build_prompt_inputs(
        lines=lines,
        flag_codes=flag_codes,
        flags_desc=flags_desc,
        required_fields_map=required_fields_map,
    )

    prompt_text, meta = render_narrative_prompt(
        subs=subs,
        template_path=Path(args.template),
    )

    if args.out:
        out_path = Path(args.out)
    else:
        DEFAULT_RENDERED_DIR.mkdir(parents=True, exist_ok=True)
        out_path = DEFAULT_RENDERED_DIR / f"narrative_prompt_{args.prefix}.txt"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(prompt_text, encoding="utf-8")

    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[OK] Prompt written to: {out_path}", file=sys.stderr)
    print(f"[OK] Meta written to:   {meta_path}", file=sys.stderr)
    print(
        f"[OK] flags={meta['n_flags']} union_headers={meta['n_union_headers']} csv_chars={meta['csv_chars']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
