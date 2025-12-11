from __future__ import annotations
import os
import sys
import json
import csv
import argparse
import importlib.util
from pathlib import Path

from rules_runtime import rule, RuleResult, parse_date, days_between, safe_float

ROOT = Path(__file__).resolve().parent
RULES_DIR = ROOT / "rules_out"
DEFAULT_CSV = ROOT / "invoices_to_check.csv"
UTBMS_CSV = ROOT / "data" / "utbms.csv"

# CSV global contenant toutes les lignes de factures (historique complet)
GLOBAL_INVOICE_CSV = ROOT / "data" / "invoices.csv"


# ============================================================
# Chargement dynamique des règles (fichiers .py dans rules_out/)
# ============================================================
def load_all_rules():
    rules = []
    if not RULES_DIR.exists():
        print(f"[WARN] Rules directory does not exist: {RULES_DIR}", file=sys.stderr)
        return rules

    for py_file in RULES_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue

        spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
        if spec is None or spec.loader is None:
            print(f"[ERR] Could not create spec for {py_file}", file=sys.stderr)
            continue

        module = importlib.util.module_from_spec(spec)

        # Injection des symboles nécessaires dans le namespace du module
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
            print(f"[ERR] Failed loading rule file {py_file}: {e}", file=sys.stderr)
            continue

        for attr in dir(module):
            obj = getattr(module, attr)
            if callable(obj) and hasattr(obj, "_rule_code"):
                rules.append(obj)

    return rules


# ============================================================
# UTBMS : chargement & jointure
# ============================================================
def load_utbms_lookup():
    """
    Charge le fichier data/utbms.csv s'il existe.
    Format attendu : CATEGORY,SUBCATEGORY,CODE,DESCRIPTION
    Retourne un dict : code -> row_dict
    """
    if not UTBMS_CSV.exists():
        print(f"[WARN] UTBMS file not found: {UTBMS_CSV} (UTBMS fields will be missing)", file=sys.stderr)
        return {}

    lookup = {}
    with UTBMS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("CODE") or "").strip()
            if not code:
                continue
            lookup[code] = row
    return lookup


def enrich_line_with_utbms(line: dict, utbms_lookup: dict):
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
        # Pas trouvé dans la table UTBMS, on met juste le code brut si absent
        line.setdefault("UTBMS_CODE", code)
        return line

    line["UTBMS_CODE"] = code
    line["UTBMS_CATEGORY"] = utbms_info.get("CATEGORY", "")
    line["UTBMS_SUBCATEGORY"] = utbms_info.get("SUBCATEGORY", "")
    line["UTBMS_DESCRIPTION"] = utbms_info.get("DESCRIPTION", "")

    # Famille heuristique
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
# Chargement global : toutes les lignes de factures (invoices.csv)
# ============================================================
def load_global_invoice_lines(utbms_lookup: dict) -> list[dict]:
    """
    Charge TOUTES les lignes depuis data/invoices.csv (si présent),
    et les enrichit avec UTBMS.
    Retourne une liste de dicts représentant toutes les lignes disponibles.
    """
    if not GLOBAL_INVOICE_CSV.exists():
        print(f"[WARN] Global invoice CSV not found: {GLOBAL_INVOICE_CSV}", file=sys.stderr)
        return []

    lines: list[dict] = []
    with GLOBAL_INVOICE_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            line = dict(row)
            line = enrich_line_with_utbms(line, utbms_lookup)
            lines.append(line)

    print(f"[INFO] Loaded {len(lines)} global invoice lines from {GLOBAL_INVOICE_CSV}", file=sys.stderr)
    return lines


# ============================================================
# Lecture du CSV “lignes en vrac” (fichier à checker)
# ============================================================
def load_lines_from_csv(csv_path: Path, utbms_lookup: dict):
    """
    Lit un CSV avec des lignes en vrac (une ligne de facture par row).
    Retourne une liste de dicts (une dict = une ligne),
    en les enrichissant avec UTBMS.
    """
    if not csv_path.exists():
        raise SystemExit(f"[ERR] CSV file not found: {csv_path}")

    lines = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            line = dict(row)
            line = enrich_line_with_utbms(line, utbms_lookup)
            lines.append(line)

    return lines


# ============================================================
# Construction du contexte global ALL_LINES (Option A: 1 ligne / KID)
# ============================================================
def build_all_context_lines(global_lines: list[dict], sample_lines: list[dict]) -> list[dict]:
    """
    Construit la liste ALL_LINES utilisée par les règles.

    ALL_LINES = historique (global_lines) + invoices_to_check (sample_lines),
    avec UNE SEULE ligne par KID (peu importe la source).
    """
    all_lines: list[dict] = []
    seen_kids: set[str] = set()

    # 1) Ajouter les global_lines, dédupliqué par KID
    for ln in global_lines:
        kid = (ln.get("KID") or "").strip()
        if kid and kid in seen_kids:
            continue
        all_lines.append(ln)
        if kid:
            seen_kids.add(kid)

    # 2) Ajouter les sample_lines, dédupliqué aussi par KID (vs global + local)
    for ln in sample_lines:
        kid = (ln.get("KID") or "").strip()
        if kid and kid in seen_kids:
            # déjà vue (global ou local), on ne la rajoute pas
            continue
        all_lines.append(ln)
        if kid:
            seen_kids.add(kid)

    return all_lines


# ============================================================
# Application des règles à une ligne
# ============================================================
def apply_rules_to_line(line: dict, rules, debug: bool = False):
    results = []

    for rule_func in rules:
        code = getattr(rule_func, "_rule_code", rule_func.__name__)

        try:
            out = rule_func(line)

            if out is None:
                continue

            if not isinstance(out, RuleResult):
                results.append({
                    "code": code,
                    "triggered": False,
                    "error": f"Invalid return type from rule {code}"
                })
                continue

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
            if debug:
                raise
            results.append({
                "code": code,
                "triggered": False,
                "error": str(e),
            })

    return results


# ============================================================
# Groupement par facture (INVOICE_NUMBER)
# ============================================================
def group_lines_by_invoice(lines):
    """
    Regroupe les lignes par INVOICE_NUMBER.
    Retourne dict: invoice_number -> list[lines]
    """
    invoices = {}
    for line in lines:
        inv_no = line.get("INVOICE_NUMBER") or line.get("invoice_number") or ""
        inv_no = str(inv_no).strip()
        if inv_no not in invoices:
            invoices[inv_no] = []
        invoices[inv_no].append(line)
    return invoices


# ============================================================
# Application des règles avec ALL_LINES global
# ============================================================
def apply_rules_to_invoices_from_lines(lines, rules, all_context_lines, debug: bool = False):
    """
    Prend des lignes “en vrac”, les groupe par facture, injecte ALL_LINES
    (historiques + invoices_to_check, dédupliqué par KID) et applique les règles.

    - lines : lignes à vérifier (échantillon / une seule facture / une seule ligne)
    - all_context_lines : ALL_LINES commun à toutes les lignes
    """
    by_invoice = group_lines_by_invoice(lines)
    results = []

    for invoice_number, inv_lines in by_invoice.items():
        enriched = []
        for ln in inv_lines:
            ln_copy = dict(ln)
            ln_copy["ALL_LINES"] = all_context_lines
            enriched.append(ln_copy)

        per_line_results = []
        for ln in enriched:
            res = apply_rules_to_line(ln, rules, debug=debug)
            per_line_results.append({
                "KID": ln.get("KID"),
                "LINE_ITEM_NUMBER": ln.get("LINE_ITEM_NUMBER"),
                "INVOICE_NUMBER": ln.get("INVOICE_NUMBER"),
                "LAW_FIRM_MATTER_ID": ln.get("LAW_FIRM_MATTER_ID"),
                "results": res,
            })

        results.append({
            "invoice_number": invoice_number,
            "results_per_line": per_line_results,
        })

    return results


# ============================================================
# Flatten : une entrée par KID avec la liste des flags appliqués
# ============================================================
def flatten_results_per_line(nested_results: list[dict]) -> list[dict]:
    """
    Transforme la structure imbriquée :
      [ { invoice_number, results_per_line: [ {KID, ..., results:[...]}, ... ] }, ... ]
    en une liste plate :
      [ { KID, INVOICE_NUMBER, LINE_ITEM_NUMBER, LAW_FIRM_MATTER_ID, flags:[...] }, ... ]

    flags = uniquement les règles pour lesquelles triggered == True.
    """
    flat: list[dict] = []

    for invoice_block in nested_results:
        inv_no = invoice_block.get("invoice_number")
        for line_res in invoice_block.get("results_per_line", []):
            kid = line_res.get("KID")
            line_no = line_res.get("LINE_ITEM_NUMBER")
            matter_id = line_res.get("LAW_FIRM_MATTER_ID")

            triggered_flags = []
            for r in line_res.get("results", []):
                if not r.get("triggered"):
                    continue
                triggered_flags.append({
                    "code": r.get("code"),
                    "penalty": r.get("penalty"),
                    "message": r.get("message"),
                    "score": r.get("score"),
                    "meta": r.get("meta"),
                    "error": r.get("error"),
                })

            flat.append({
                "KID": kid,
                "INVOICE_NUMBER": line_res.get("INVOICE_NUMBER", inv_no),
                "LINE_ITEM_NUMBER": line_no,
                "LAW_FIRM_MATTER_ID": matter_id,
                "flags": triggered_flags,
            })

    return flat


# ============================================================
# CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Run billing rule engine on CSV invoice lines.")
    parser.add_argument(
        "--csv",
        type=str,
        default=str(DEFAULT_CSV),
        help=f"Path to CSV file with invoice lines (default: {DEFAULT_CSV.name})"
    )
    parser.add_argument("--debug", action="store_true", help="Re-raise exceptions from rules")
    args = parser.parse_args()

    csv_path = Path(args.csv)

    # 1) Charger les règles
    print("[INFO] Loading rules…", file=sys.stderr)
    rules = load_all_rules()
    print(f"[INFO] {len(rules)} rules loaded.", file=sys.stderr)
    if not rules:
        print("[WARN] No rules loaded. Exiting.", file=sys.stderr)
        print("[]")
        sys.exit(0)

    # 2) Charger UTBMS
    print("[INFO] Loading UTBMS lookup…", file=sys.stderr)
    utbms_lookup = load_utbms_lookup()
    print(f"[INFO] {len(utbms_lookup)} UTBMS codes loaded.", file=sys.stderr)

    # 3) Charger les lignes globales (historique complet)
    print("[INFO] Loading global invoice lines…", file=sys.stderr)
    global_lines = load_global_invoice_lines(utbms_lookup)
    print(f"[INFO] {len(global_lines)} global invoice lines loaded.", file=sys.stderr)

    # 4) Charger les lignes du CSV ciblé (échantillon / une seule facture / une seule ligne)
    print(f"[INFO] Loading lines from {csv_path}…", file=sys.stderr)
    lines = load_lines_from_csv(csv_path, utbms_lookup)
    print(f"[INFO] {len(lines)} lines loaded from CSV.", file=sys.stderr)

    if not lines:
        print("[WARN] No lines to process. Exiting.", file=sys.stderr)
        print("[]")
        sys.exit(0)

    # 5) Construire ALL_LINES = historique + invoices_to_check (1 ligne par KID)
    all_context_lines = build_all_context_lines(global_lines, lines)
    print(f"[INFO] ALL_LINES context size: {len(all_context_lines)}", file=sys.stderr)

    # 6) Appliquer les règles par facture (structure imbriquée)
    nested_results = apply_rules_to_invoices_from_lines(
        lines,
        rules,
        all_context_lines,
        debug=args.debug,
    )

    # 7) Aplatir : une entrée par KID avec la liste des flags appliqués
    flat_results = flatten_results_per_line(nested_results)

    # 8) Sortie JSON compacte
    print(json.dumps(flat_results, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
