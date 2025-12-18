from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# ============================================================
# Paths bootstrap
# ============================================================
CURRENT_FILE = Path(__file__).resolve()
CORE_DIR = CURRENT_FILE.parent
NARRATIVE_ROOT = CORE_DIR.parent
PROJECT_ROOT = CORE_DIR.parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================
# Debug paths
# ============================================================
DEBUG_ROOT = NARRATIVE_ROOT / "artifacts" / "debug"
DEBUG_PROMPTS = DEBUG_ROOT / "prompts"
DEBUG_RESPONSES = DEBUG_ROOT / "responses"

# ============================================================
# Local helpers
# ============================================================
def chunk(items: List[Any], n: int) -> Iterable[List[Any]]:
    if n <= 0:
        n = 1
    for i in range(0, len(items), n):
        yield items[i : i + n]


def log(level: str, msg: str) -> None:
    print(f"[NARRATIVE][{level}] {msg}", file=sys.stderr)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_flag_codes(flag_codes: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for c in flag_codes or []:
        c = (c or "").strip().upper()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _load_required_fields_map(path: Path) -> Dict[str, List[str]]:
    data = _read_json(path)
    out: Dict[str, List[str]] = {}
    if not isinstance(data, dict):
        return out
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, list):
            out[k.strip().upper()] = [f.strip() for f in v if isinstance(f, str) and f.strip()]
    return out


def _load_flags_desc_from_flags_csv(path: Path) -> Dict[str, str]:
    """
    data/flags.csv
    Headers:
      GROUP NAME,FLAG ABBR,FLAG NAME,FLAG DESCRIPTION,FLAG PENALTY__
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing flags CSV: {path}")

    out: Dict[str, str] = {}
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            abbr = (row.get("FLAG ABBR") or "").strip().upper()
            if not abbr:
                continue
            name = (row.get("FLAG NAME") or "").strip()
            desc = (row.get("FLAG DESCRIPTION") or "").strip()
            out[abbr] = f"{name} | {desc}".strip(" |")
    return out


def _load_lines_from_invoices_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing invoices CSV: {path}")
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


# ============================================================
# Token + cost estimation (conservative)
# ============================================================
def estimate_tokens_from_text(text: str, safety_mult: float = 1.15) -> int:
    return int(math.ceil((len(text) / 4.0) * safety_mult))


def estimate_cost_usd(tokens_in: int, tokens_out: int, price_in_per_1k: float, price_out_per_1k: float) -> float:
    return (tokens_in / 1000.0) * price_in_per_1k + (tokens_out / 1000.0) * price_out_per_1k


def cost_from_usage_usd(
    usage: Dict[str, Any],
    *,
    price_in_per_1k: float,
    price_out_per_1k: float,
) -> Optional[float]:
    """
    Uses real usage tokens when present.
    For OpenAI-like usage: prompt_tokens + completion_tokens.
    """
    if not isinstance(usage, dict):
        return None
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    if not isinstance(pt, int) or not isinstance(ct, int):
        return None
    return (pt / 1000.0) * price_in_per_1k + (ct / 1000.0) * price_out_per_1k


# ============================================================
# Ledger (budget hard-cap)
# ============================================================
def load_ledger(path: Path, *, budget_eur: float) -> Dict[str, Any]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                data.setdefault("calls", [])
                data.setdefault("spent_eur_est", 0.0)
                data.setdefault("spent_eur_real", 0.0)
                data.setdefault("budget_eur", float(budget_eur))
                return data
        except Exception:
            pass
    return {
        "budget_eur": float(budget_eur),
        "spent_eur_est": 0.0,
        "spent_eur_real": 0.0,
        "calls": [],
    }


def save_ledger(path: Path, ledger: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ============================================================
# Project imports
# ============================================================
from narrative.core.prompt_builder import build_prompt_inputs, render_narrative_prompt
from app_utils_bedrock import call_bedrock_json


# ============================================================
# Defaults
# ============================================================

safe_guard120b = "openai.gpt-oss-20b-1:0"
classic_120b = "openai.gpt-oss-120b-1:0"
classic_20b = "openai.gpt-oss-20b-1:0"

DEFAULT_MODEL_ID = classic_120b

DEFAULT_INVOICES_CSV = NARRATIVE_ROOT / "artifacts" / "sample_input" / "invoices_to_check.csv"
DEFAULT_REQUIRED_FIELDS_JSON = NARRATIVE_ROOT / "resources" / "required_fields.json"
DEFAULT_FLAGS_CSV = PROJECT_ROOT / "data" / "flags.csv"
DEFAULT_TEMPLATE = NARRATIVE_ROOT / "resources" / "prompts" / "narrative_batch_csv.txt"

DEFAULT_OUT_DIR = NARRATIVE_ROOT / "artifacts" / "generated"
DEFAULT_OUT_JSON = DEFAULT_OUT_DIR / "narrative_results.json"
DEFAULT_RAW_JSONL = NARRATIVE_ROOT / "artifacts" / "narrative_llm_raw.jsonl"
DEFAULT_LEDGER = DEFAULT_OUT_DIR / "spend_ledger.json"


# ============================================================
# Core
# ============================================================
def run_narrative_grouped(
    *,
    lines: List[Dict[str, Any]],
    narrative_codes: List[str],
    flags_desc: Dict[str, str],
    required_fields_map: Dict[str, List[str]],
    template_path: Path,
    model_id: str,
    batch_lines: int,
    batch_flags: int,
    raw_log_path: Path,
    dry_run: bool,
    budget_eur: float,
    ledger_path: Path,
    price_in_per_1k_usd: float,
    price_out_per_1k_usd: float,
    usd_to_eur: float,
    max_output_tokens: int,
    token_safety_mult: float,
) -> Dict[str, List[List[str]]]:

    out: Dict[str, List[List[str]]] = {}
    narrative_codes = _clean_flag_codes(narrative_codes)

    DEBUG_PROMPTS.mkdir(parents=True, exist_ok=True)
    DEBUG_RESPONSES.mkdir(parents=True, exist_ok=True)

    ledger = load_ledger(ledger_path, budget_eur=budget_eur)
    spent_eur_est = float(ledger.get("spent_eur_est", 0.0))
    spent_eur_real = float(ledger.get("spent_eur_real", 0.0))
    call_index = len(ledger.get("calls", []))

    for lines_batch in chunk(lines, batch_lines):
        for flags_batch in chunk(narrative_codes, batch_flags):
            call_index += 1

            subs = build_prompt_inputs(
                lines=lines_batch,
                flag_codes=list(flags_batch),
                flags_desc=flags_desc,
                required_fields_map=required_fields_map,
            )

            prompt_text, _ = render_narrative_prompt(subs=subs, template_path=template_path)

            # ---- prompt dump
            (DEBUG_PROMPTS / f"prompt_{call_index:04d}.txt").write_text(prompt_text, encoding="utf-8")
            (DEBUG_PROMPTS / f"prompt_{call_index:04d}.meta.json").write_text(
                json.dumps(
                    {
                        "call_index": call_index,
                        "n_lines": len(lines_batch),
                        "flag_codes": list(flags_batch),
                        "model_id": model_id,
                        "template": str(template_path),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            # ---- estimation (pre-call)
            tokens_in_est = estimate_tokens_from_text(prompt_text, token_safety_mult)
            tokens_out_est = max_output_tokens
            cost_usd_est = estimate_cost_usd(tokens_in_est, tokens_out_est, price_in_per_1k_usd, price_out_per_1k_usd)
            cost_eur_est = cost_usd_est * usd_to_eur

            log(
                "INFO",
                f"call#{call_index} lines={len(lines_batch)} flags={len(list(flags_batch))} "
                f"tokens_in_est={tokens_in_est} cost_est_eur={cost_eur_est:.6f} "
                f"spent_est_eur={spent_eur_est:.6f} spent_real_eur={spent_eur_real:.6f}",
            )

            if spent_eur_est + cost_eur_est > budget_eur:
                log("WARN", "BUDGET CAP reached — stopping execution")
                ledger["spent_eur_est"] = spent_eur_est
                ledger["spent_eur_real"] = spent_eur_real
                save_ledger(ledger_path, ledger)
                return out

            # record call in ledger (estimates first)
            ledger_call = {
                "ts_utc": utc_now_iso(),
                "call_index": call_index,
                "n_lines": len(lines_batch),
                "n_flags": len(list(flags_batch)),
                "flag_codes": list(flags_batch),
                "tokens_in_est": tokens_in_est,
                "max_output_tokens": max_output_tokens,
                "cost_eur_est": cost_eur_est,
                "tokens_in_real": None,
                "tokens_out_real": None,
                "cost_eur_real": None,
            }
            ledger.setdefault("calls", []).append(ledger_call)
            spent_eur_est += cost_eur_est
            ledger["spent_eur_est"] = spent_eur_est
            save_ledger(ledger_path, ledger)

            if dry_run:
                continue

            data, usage = call_bedrock_json(
                prompt=prompt_text,
                model_id=model_id,
                raw_log_path=raw_log_path,
                max_output_tokens=max_output_tokens,
                temperature=0.0,
                debug_dir=DEBUG_RESPONSES,
                call_index=call_index,
            )

            # ---- reconcile real cost from usage (best-effort)
            if isinstance(usage, dict):
                pt = usage.get("prompt_tokens")
                ct = usage.get("completion_tokens")
                if isinstance(pt, int) and isinstance(ct, int):
                    real_usd = cost_from_usage_usd(usage, price_in_per_1k=price_in_per_1k_usd, price_out_per_1k=price_out_per_1k_usd)
                    if isinstance(real_usd, float):
                        real_eur = real_usd * usd_to_eur
                        spent_eur_real += real_eur

                        ledger_call["tokens_in_real"] = pt
                        ledger_call["tokens_out_real"] = ct
                        ledger_call["cost_eur_real"] = real_eur

                        ledger["spent_eur_real"] = spent_eur_real
                        save_ledger(ledger_path, ledger)

            if not isinstance(data, dict):
                log("WARN", f"call#{call_index} invalid/empty model JSON")
                continue

            for flag_code, groups in data.items():
                if not isinstance(groups, list):
                    continue
                out.setdefault(flag_code, [])
                for g in groups:
                    if isinstance(g, list) and g:
                        out[flag_code].append([k for k in g if isinstance(k, str)])

    ledger["spent_eur_est"] = spent_eur_est
    ledger["spent_eur_real"] = spent_eur_real
    save_ledger(ledger_path, ledger)
    return out


# ============================================================
# CLI
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Narrative LLM runner (Bedrock, debug + real usage costing)")

    parser.add_argument("--batch-lines", type=int, default=int(os.environ.get("NARR_LLM_BATCH_LINES", "50")))
    parser.add_argument("--batch-flags", type=int, default=int(os.environ.get("NARR_LLM_BATCH_FLAGS", "50")))
    parser.add_argument("--budget-eur", type=float, default=float(os.environ.get("NARR_BUDGET_EUR", "5.0")))
    parser.add_argument("--dry-run", action="store_true")

    # pricing knobs (default = tes valeurs actuelles)
    parser.add_argument("--price-in-per-1k-usd", type=float, default=float(os.environ.get("NARR_PRICE_IN_1K_USD", "0.00008")))
    parser.add_argument("--price-out-per-1k-usd", type=float, default=float(os.environ.get("NARR_PRICE_OUT_1K_USD", "0.00023")))
    parser.add_argument("--usd-to-eur", type=float, default=float(os.environ.get("NARR_USD_TO_EUR", "0.92")))
    parser.add_argument("--max-output-tokens", type=int, default=int(os.environ.get("NARR_MAX_OUTPUT_TOKENS", "100000")))
    parser.add_argument("--token-safety-mult", type=float, default=float(os.environ.get("NARR_TOKEN_SAFETY_MULT", "1.15")))

    args = parser.parse_args()

    lines = _load_lines_from_invoices_csv(DEFAULT_INVOICES_CSV)
    required_fields_map = _load_required_fields_map(DEFAULT_REQUIRED_FIELDS_JSON)
    flags_desc = _load_flags_desc_from_flags_csv(DEFAULT_FLAGS_CSV)
    narrative_codes = sorted(required_fields_map.keys())

    log("INFO", f"ModelId: {DEFAULT_MODEL_ID}")
    log("INFO", f"Lines: {len(lines)} Flags: {len(narrative_codes)}")
    log("INFO", f"Batching: lines={args.batch_lines} flags={args.batch_flags}")
    log("INFO", f"Budget EUR: {args.budget_eur} DryRun={args.dry_run}")
    log("INFO", f"Debug dir: {DEBUG_ROOT}")

    results = run_narrative_grouped(
        lines=lines,
        narrative_codes=narrative_codes,
        flags_desc=flags_desc,
        required_fields_map=required_fields_map,
        template_path=DEFAULT_TEMPLATE,
        model_id=DEFAULT_MODEL_ID,
        batch_lines=args.batch_lines,
        batch_flags=args.batch_flags,
        raw_log_path=DEFAULT_RAW_JSONL,
        dry_run=args.dry_run,
        budget_eur=args.budget_eur,
        ledger_path=DEFAULT_LEDGER,
        price_in_per_1k_usd=args.price_in_per_1k_usd,
        price_out_per_1k_usd=args.price_out_per_1k_usd,
        usd_to_eur=args.usd_to_eur,
        max_output_tokens=args.max_output_tokens,
        token_safety_mult=args.token_safety_mult,
    )

    DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUT_JSON.write_text(
        json.dumps(
            {
                "meta": {
                    "created_at_utc": utc_now_iso(),
                    "model_id": DEFAULT_MODEL_ID,
                    "dry_run": args.dry_run,
                    "debug_dir": str(DEBUG_ROOT),
                },
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    log("INFO", f"Results written to {DEFAULT_OUT_JSON}")
    log("INFO", f"Ledger written to  {DEFAULT_LEDGER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
