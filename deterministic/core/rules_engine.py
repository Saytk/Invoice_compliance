from __future__ import annotations

import os
import sys
import json
import csv
import argparse
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Callable, Optional, Tuple

CURRENT_FILE = Path(__file__).resolve()
DETERMINISTIC_ROOT = CURRENT_FILE.parents[1]
PROJECT_ROOT = CURRENT_FILE.parents[2]

# Allow running as a script (python deterministic/core/rules_engine.py)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deterministic.core.rules_runtime import rule, RuleResult, parse_date, days_between, safe_float

# ============================================================
# Paths
# ============================================================
ROOT = DETERMINISTIC_ROOT
RULES_DIR = ROOT / "artifacts" / "rules_out"
DEFAULT_CSV = ROOT / "resources" / "invoices_to_check.csv"
UTBMS_CSV = ROOT / "resources" / "data" / "utbms.csv"
GLOBAL_INVOICES_CSV = ROOT / "resources" / "data" / "invoices.csv"
FLAGS_CATEGORIES_JSON = ROOT / "resources" / "data" / "flags_engine_categories.json"

# ============================================================
# Logging (multi-niveaux) + hook GUI
# ============================================================

LOG_LEVELS = {
    "ERROR": 0,
    "WARN": 1,
    "INFO": 2,
    "DEBUG": 3,
    "TRACE": 4,
}

_env_level = os.environ.get("INVOICE_LOG_LEVEL", "INFO").upper()
LOG_LEVEL = LOG_LEVELS.get(_env_level, LOG_LEVELS["INFO"])

GUI_LOGGER = None  # type: ignore[assignment]


def set_log_level(level_name: str) -> None:
    global LOG_LEVEL
    level_name = level_name.upper()
    LOG_LEVEL = LOG_LEVELS.get(level_name, LOG_LEVELS["INFO"])


def _should_log(level_name: str) -> bool:
    return LOG_LEVELS.get(level_name, 999) <= LOG_LEVEL


def attach_gui_logger(logger) -> None:
    global GUI_LOGGER
    GUI_LOGGER = logger
    if _should_log("DEBUG"):
        print("[DEBUG] GUI logger attached to rules_engine", file=sys.stderr)


def log(level: str, msg: str) -> None:
    if not _should_log(level):
        return

    full = f"[{level}] {msg}"
    print(full, file=sys.stderr)

    if GUI_LOGGER is not None:
        try:
            GUI_LOGGER.write(full)
        except Exception:
            pass


def log_error(msg: str) -> None:
    log("ERROR", msg)


def log_warn(msg: str) -> None:
    log("WARN", msg)


def log_info(msg: str) -> None:
    log("INFO", msg)


def log_debug(msg: str) -> None:
    log("DEBUG", msg)


def log_trace(msg: str) -> None:
    log("TRACE", msg)


# ============================================================
# Chargement dynamique des règles (fichiers .py dans rules_out/)
# ============================================================

def load_all_rules():
    rules = []
    if not RULES_DIR.exists():
        log_warn(f"Rules directory does not exist: {RULES_DIR}")
        return rules

    log_info(f"Loading rules from: {RULES_DIR}")
    for py_file in RULES_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            log_debug(f"Skipping private rule file: {py_file.name}")
            continue

        log_debug(f"Loading rule module from: {py_file}")
        spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
        if spec is None or spec.loader is None:
            log_error(f"Could not create spec for {py_file}")
            continue

        module = importlib.util.module_from_spec(spec)

        module.__dict__.update({
            "rule": rule,
            "RuleResult": RuleResult,
            "parse_date": parse_date,
            "days_between": days_between,
            "safe_float": safe_float,
        })

        try:
            spec.loader.exec_module(module)
        except Exception as e:
            log_error(f"Failed loading rule file {py_file}: {e}")
            if _should_log("DEBUG"):
                import traceback
                traceback.print_exc()
            continue

        count_before = len(rules)
        for attr in dir(module):
            obj = getattr(module, attr)
            if callable(obj) and hasattr(obj, "_rule_code"):
                rules.append(obj)
                log_debug(f"  Registered rule: {obj._rule_code} ({attr})")

        if len(rules) == count_before:
            log_trace(f"No rules found in module {py_file.name}")

    log_info(f"{len(rules)} rules loaded.")
    if _should_log("TRACE"):
        for r in rules:
            log_trace(f"Rule loaded: code={getattr(r, '_rule_code', '?')} func={r.__name__}")

    return rules


# ============================================================
# Flags categories (STRUCTURED / NARRATIVE_LLM / IGNORED_OR_PREPROCESS)
# ============================================================

def load_flags_categories() -> Dict[str, str]:
    """
    Charge data/flags_engine_categories.json si présent.
    Retour: code -> category
    """
    if not FLAGS_CATEGORIES_JSON.exists():
        log_warn(f"Flags categories file not found: {FLAGS_CATEGORIES_JSON} (all treated as STRUCTURED by default)")
        return {}

    try:
        data = json.loads(FLAGS_CATEGORIES_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        log_warn(f"Could not parse {FLAGS_CATEGORIES_JSON.name}: {e} (all treated as STRUCTURED by default)")
        return {}

    if not isinstance(data, dict):
        log_warn(f"{FLAGS_CATEGORIES_JSON.name} must be a JSON object {{code: category}}. Got: {type(data).__name__}")
        return {}

    out: Dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        code = k.strip().upper()
        cat = v.strip().upper()
        if code:
            out[code] = cat
    log_info(f"Loaded {len(out)} flag categories from {FLAGS_CATEGORIES_JSON}")
    return out


def get_flag_category(code: str, categories: Dict[str, str]) -> str:
    """
    Par défaut, tout ce qui n'est pas dans le JSON est STRUCTURED.
    """
    return categories.get((code or "").strip().upper(), "STRUCTURED")


def split_rules_by_category(
    rules,
    categories: Dict[str, str],
) -> Tuple[list, list, list]:
    """
    Retourne (structured_rules, narrative_rules, ignored_rules).
    - ignored_rules = ceux dont la category JSON est IGNORED_OR_PREPROCESS
      (ils ne devraient normalement pas être dans rules_out, mais on gère au cas où)
    """
    structured = []
    narrative = []
    ignored = []

    for r in rules:
        code = getattr(r, "_rule_code", r.__name__)
        cat = get_flag_category(str(code), categories)

        if cat == "IGNORED_OR_PREPROCESS":
            ignored.append(r)
        elif cat == "NARRATIVE_LLM":
            narrative.append(r)
        else:
            structured.append(r)

    return structured, narrative, ignored


# ============================================================
# UTBMS : chargement & jointure
# ============================================================

def load_utbms_lookup() -> Dict[str, Dict[str, str]]:
    """
    Charge le fichier data/utbms.csv s'il existe.
    Format attendu : CATEGORY,SUBCATEGORY,CODE,DESCRIPTION
    Retourne un dict : code -> row_dict
    """
    if not UTBMS_CSV.exists():
        log_warn(f"UTBMS file not found: {UTBMS_CSV} (UTBMS fields will be missing)")
        return {}

    lookup: Dict[str, Dict[str, str]] = {}
    with UTBMS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("CODE") or "").strip()
            if not code:
                continue
            lookup[code] = row

    log_info(f"{len(lookup)} UTBMS codes loaded from {UTBMS_CSV}")
    return lookup


def enrich_line_with_utbms(line: dict, utbms_lookup: dict) -> dict:
    """
    Si UTBMS_CODE n'est pas déjà présent, on le déduit de :
    - LINE_ITEM_TASK_CODE
    - LINE_ITEM_ACTIVITY_CODE
    - LINE_ITEM_EXPENSE_CODE
    dans cet ordre, puis on joint avec le lookup UTBMS.
    """
    if "UTBMS_CODE" in line and line.get("UTBMS_CODE"):
        code = line["UTBMS_CODE"]
    else:
        code = (
            (line.get("LINE_ITEM_TASK_CODE") or "").strip()
            or (line.get("LINE_ITEM_ACTIVITY_CODE") or "").strip()
            or (line.get("LINE_ITEM_EXPENSE_CODE") or "").strip()
        )

    if not code:
        return line

    utbms_info = utbms_lookup.get(code)
    if not utbms_info:
        line.setdefault("UTBMS_CODE", code)
        return line

    line["UTBMS_CODE"] = code
    line["UTBMS_CATEGORY"] = utbms_info.get("CATEGORY", "")
    line["UTBMS_SUBCATEGORY"] = utbms_info.get("SUBCATEGORY", "")
    line["UTBMS_DESCRIPTION"] = utbms_info.get("DESCRIPTION", "")

    if code == (line.get("LINE_ITEM_TASK_CODE") or "").strip():
        family = "TASK"
    elif code == (line.get("LINE_ITEM_ACTIVITY_CODE") or "").strip():
        family = "ACTIVITY"
    elif code == (line.get("LINE_ITEM_EXPENSE_CODE") or "").strip():
        family = "EXPENSE"
    else:
        family = ""
    line["UTBMS_FAMILY"] = family

    return line


# ============================================================
# Lecture CSV générique
# ============================================================

def load_lines_from_csv(csv_path: Path, utbms_lookup: dict, label: str) -> List[Dict[str, Any]]:
    if not csv_path.exists():
        log_error(f"CSV file not found for {label}: {csv_path}")
        return []

    log_info(f"Loading {label} lines from {csv_path}…")
    lines: List[Dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            line = dict(row)
            line = enrich_line_with_utbms(line, utbms_lookup)
            lines.append(line)

    log_info(f"Loaded {len(lines)} {label} lines from {csv_path}")
    if _should_log("TRACE"):
        for i, ln in enumerate(lines[:5]):
            log_trace(f"[{label}] Sample line #{i}: KID={ln.get('KID')} INV={ln.get('INVOICE_NUMBER')}")

    return lines


def load_global_invoice_lines(utbms_lookup: dict) -> List[Dict[str, Any]]:
    return load_lines_from_csv(GLOBAL_INVOICES_CSV, utbms_lookup, label="global")


# ============================================================
# Construction du contexte ALL_LINES
# ============================================================

def build_all_lines_context(
    global_lines: List[Dict[str, Any]],
    custom_lines: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    log_info(
        f"Combining global lines ({len(global_lines)}) + custom lines ({len(custom_lines)}) "
        f"into ALL_LINES context…"
    )

    all_lines = list(global_lines) + list(custom_lines)

    log_info(f"ALL_LINES context size: {len(all_lines)}")

    if _should_log("DEBUG"):
        by_invoice: Dict[str, int] = {}
        by_tk: Dict[str, int] = {}
        by_matter: Dict[str, int] = {}

        for ln in all_lines:
            inv = (ln.get("INVOICE_NUMBER") or "").strip() or "<EMPTY>"
            tk = (ln.get("TIMEKEEPER_ID") or "").strip() or "<EMPTY>"
            matter = (ln.get("LAW_FIRM_MATTER_ID") or "").strip() or "<EMPTY>"

            by_invoice[inv] = by_invoice.get(inv, 0) + 1
            by_tk[tk] = by_tk.get(tk, 0) + 1
            by_matter[matter] = by_matter.get(matter, 0) + 1

        log_debug("[DEBUG] --- ALL_LINES Summary by invoice ---")
        for inv, count in sorted(by_invoice.items()):
            log_debug(f"[DEBUG]   Invoice {inv}: {count} lines")

        log_debug("[DEBUG] --- ALL_LINES Summary by timekeeper ---")
        for tk, count in sorted(by_tk.items()):
            log_debug(f"[DEBUG]   TK {tk}: {count} lines")

        log_debug("[DEBUG] --- ALL_LINES Summary by matter ---")
        for mat, count in sorted(by_matter.items()):
            log_debug(f"[DEBUG]   Matter {mat}: {count} lines")

    if _should_log("TRACE"):
        log_trace("[TRACE] First 5 ALL_LINES entries:")
        for ln in all_lines[:5]:
            log_trace(
                f"  KID={ln.get('KID')} INV={ln.get('INVOICE_NUMBER')} "
                f"TK={ln.get('TIMEKEEPER_ID')} DATE={ln.get('LINE_ITEM_DATE')}"
            )

    return all_lines


# ============================================================
# Application des règles à une ligne (STRUCTURED uniquement)
# ============================================================

def apply_rules_to_line(line: dict, rules, debug_errors: bool = False):
    """
    Applique toutes les règles (déterministes) à une ligne.
    Retourne une liste d'objets "flag" (dicts) pour cette ligne.
    """
    results = []
    kid = line.get("KID")
    inv = line.get("INVOICE_NUMBER")

    log_debug(f"Applying {len(rules)} rules to KID={kid} INV={inv}")

    for rule_func in rules:
        code = getattr(rule_func, "_rule_code", rule_func.__name__)

        try:
            out = rule_func(line)

            if out is None:
                log_trace(f"[TRACE] Rule {code} -> None for KID={kid}")
                continue

            if not isinstance(out, RuleResult):
                msg = f"Invalid return type from rule {code}"
                log_warn(msg)
                results.append({
                    "code": str(code),
                    "triggered": False,
                    "penalty": None,
                    "message": msg,
                    "score": None,
                    "meta": None,
                    "error": msg,
                })
                continue

            log_debug(
                f"Rule {code} TRIGGERED for KID={kid}: penalty={out.penalty} msg={out.message}"
            )

            results.append({
                "code": out.code,
                "triggered": out.triggered,
                "penalty": out.penalty,
                "message": out.message,
                "score": out.score,
                "meta": out.meta,
                "error": out.error,
            })

        except Exception as e:
            err_msg = f"Exception in rule {code} for KID={kid}: {e}"
            log_error(err_msg)
            if debug_errors:
                raise
            results.append({
                "code": str(code),
                "triggered": False,
                "penalty": None,
                "message": "",
                "score": None,
                "meta": None,
                "error": err_msg,
            })

    return results


# ============================================================
# Exécution sur les lignes à vérifier (STRUCTURED + option NARRATIVE)
# ============================================================

NarrativeRunner = Callable[
    [List[Dict[str, Any]], List[str], Dict[str, Any]],
    Dict[str, List[Dict[str, Any]]]
]
# signature attendue:
# narrative_runner(lines, narrative_flag_codes, ctx) -> dict kid -> list[flag_dict]


def run_engine_on_custom_lines(
    custom_lines: List[Dict[str, Any]],
    rules,
    all_lines_context: List[Dict[str, Any]],
    debug_errors: bool = False,
    *,
    flags_categories: Optional[Dict[str, str]] = None,
    narrative_runner: Optional[NarrativeRunner] = None,
):
    """
    Pour chaque ligne:
      - injecte ALL_LINES
      - applique rules deterministes (STRUCTURED)
      - si narrative_runner fourni: traite aussi les NARRATIVE_LLM en batch
    """
    results = []

    log_info(f"Running rules on {len(custom_lines)} custom lines…")

    categories = flags_categories or {}
    structured_rules, narrative_rules, ignored_rules = split_rules_by_category(rules, categories)

    if ignored_rules:
        ig_codes = [getattr(r, "_rule_code", r.__name__) for r in ignored_rules]
        log_warn(f"Ignored rules present in rules_out (category IGNORED_OR_PREPROCESS): {ig_codes}")

    # 1) deterministic per-line
    per_line_flags: Dict[str, List[Dict[str, Any]]] = {}
    for ln in custom_lines:
        ln_kid = ln.get("KID")
        ln_inv = ln.get("INVOICE_NUMBER")
        ln_num = ln.get("LINE_ITEM_NUMBER")

        log_debug(f"Processing custom line KID={ln_kid} INV={ln_inv} LINE_ITEM_NUMBER={ln_num}")

        ln_with_ctx = dict(ln)
        ln_with_ctx["ALL_LINES"] = all_lines_context

        flags = apply_rules_to_line(ln_with_ctx, structured_rules, debug_errors=debug_errors)
        per_line_flags[str(ln_kid)] = flags if flags else []

    # 2) narrative batch (optional)
    narrative_map: Dict[str, List[Dict[str, Any]]] = {}
    if narrative_runner is not None and narrative_rules:
        narrative_codes = [str(getattr(r, "_rule_code", r.__name__)) for r in narrative_rules]
        try:
            narrative_map = narrative_runner(
                custom_lines,
                narrative_codes,
                {
                    "all_lines": all_lines_context,
                    "categories": categories,
                },
            ) or {}
            log_info(f"Narrative runner returned results for {len(narrative_map)} KIDs")
        except Exception as e:
            log_error(f"Narrative runner failed: {e}")
            narrative_map = {}

    # 3) merge output structure
    for ln in custom_lines:
        ln_kid = ln.get("KID")
        kid_key = str(ln_kid)

        flags_out = []
        flags_out.extend(per_line_flags.get(kid_key, []))
        flags_out.extend(narrative_map.get(kid_key, []))

        results.append({
            "KID": ln_kid,
            "INVOICE_NUMBER": ln.get("INVOICE_NUMBER"),
            "LINE_ITEM_NUMBER": ln.get("LINE_ITEM_NUMBER"),
            "LAW_FIRM_MATTER_ID": ln.get("LAW_FIRM_MATTER_ID"),
            "flags": flags_out if flags_out else [],
        })

    return results


# ============================================================
# API pour le client (GUI, etc.)
# ============================================================

def init_engine(
    custom_csv: str | Path | None = None,
    log_level: str | None = None,
    debug: bool = False,
) -> dict:
    """
    Point d'entrée simple pour un client (GUI / web / API).
    """
    if log_level is not None:
        set_log_level(log_level)
    if debug and LOG_LEVEL < LOG_LEVELS["DEBUG"]:
        set_log_level("DEBUG")

    log_info("Initializing engine via init_engine()…")

    rules = load_all_rules()
    categories = load_flags_categories()

    utbms_lookup = load_utbms_lookup()
    global_lines = load_global_invoice_lines(utbms_lookup)

    custom_lines: List[Dict[str, Any]] = []
    if custom_csv is not None:
        custom_lines = load_lines_from_csv(Path(custom_csv), utbms_lookup, label="custom")

    all_lines = build_all_lines_context(global_lines, custom_lines)

    return {
        "rules": rules,
        "flags_categories": categories,
        "utbms_lookup": utbms_lookup,
        "global_lines": global_lines,
        "custom_lines": custom_lines,
        "all_lines": all_lines,
    }


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Run billing rule engine on invoice lines."
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=str(DEFAULT_CSV),
        help=f"Path to CSV file with invoice lines to check (default: {DEFAULT_CSV.name})",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["ERROR", "WARN", "INFO", "DEBUG", "TRACE"],
        default=None,
        help="Logging verbosity level (overrides INVOICE_LOG_LEVEL env).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode: show stacktraces on rule errors and force at least DEBUG logging.",
    )
    args = parser.parse_args()

    if args.log_level is not None:
        set_log_level(args.log_level)
    if args.debug and LOG_LEVEL < LOG_LEVELS["DEBUG"]:
        set_log_level("DEBUG")

    log_info("Starting rules engine…")

    csv_path = Path(args.csv)

    log_info("Loading rules…")
    rules = load_all_rules()
    if not rules:
        log_warn("No rules loaded. Exiting.")
        print("[]")
        sys.exit(0)

    log_info("Loading flags categories…")
    categories = load_flags_categories()

    log_info("Loading UTBMS lookup…")
    utbms_lookup = load_utbms_lookup()

    log_info("Loading global invoice lines…")
    global_lines = load_global_invoice_lines(utbms_lookup)
    log_info(f"{len(global_lines)} global invoice lines loaded.")

    log_info(f"Loading custom lines from {csv_path}…")
    custom_lines = load_lines_from_csv(csv_path, utbms_lookup, label="custom")
    log_info(f"{len(custom_lines)} custom lines loaded from CSV.")

    all_lines = build_all_lines_context(global_lines, custom_lines)

    # Par défaut en CLI: uniquement STRUCTURED (narrative_runner=None)
    results = run_engine_on_custom_lines(
        custom_lines=custom_lines,
        rules=rules,
        all_lines_context=all_lines,
        debug_errors=args.debug,
        flags_categories=categories,
        narrative_runner=None,
    )

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
