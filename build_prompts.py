# prompts_generator.py
from __future__ import annotations
from pathlib import Path
import re, os, sys, json, csv, io
from typing import Dict, List

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

# ----------------------
# Utils de lecture texte
# ----------------------
def read_text(p: Path) -> str:
    if not p.exists() or not p.is_file():
        raise SystemExit(f"[ERR] Missing file: {p}")
    return p.read_text(encoding="utf-8").rstrip()

def parse_paths_file(p: Path) -> dict[str, str]:
    content = read_text(p)
    out: dict[str, str] = {}
    for i, raw in enumerate(content.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#",";")):
            continue
        if "=" not in line:
            raise SystemExit(f"[ERR] Invalid line in {p.name} at #{i}: expected KEY=VALUE")
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        val = os.path.expanduser(os.path.expandvars(val))
        out[key] = val
    required = {"FLAGS_PATH","UTBMS_PATH","INVOICE_PATH"}
    missing = required - set(out.keys())
    if missing:
        raise SystemExit(f"[ERR] Missing keys in {p.name}: {', '.join(sorted(missing))}")
    return out

def parse_rule_codes(file_path: Path) -> list[str]:
    raw = read_text(file_path)
    codes: list[str] = []
    for i, ln in enumerate(raw.splitlines(), 1):
        s = ln.strip()
        if not s or s.startswith(("#",";")):
            continue
        parts = [p.strip() for p in s.split(";") if p.strip()]
        for part in parts:
            m = re.search(r"([A-Za-z0-9_]{2,32})", part)
            if not m:
                raise SystemExit(f"[ERR] Invalid rule code on line {i} in {file_path.name}: {part}")
            codes.append(m.group(1).upper())
    # de-dupe en conservant l'ordre
    seen = set(); uniq = []
    for c in codes:
        if c not in seen:
            uniq.append(c); seen.add(c)
    if not uniq:
        raise SystemExit(f"[ERR] No rule codes found in {file_path}")
    return uniq

def default_penalty_for(code: str) -> float:
    m = {"ADM": 0.9, "DT": 0.0, "BB": 0.275, "VE": 0.3}
    return float(m.get(code.upper(), 0.5))

def function_name_for(code: str) -> str:
    return f"rule_{code.lower()}"

def load_flags_info(flags_path: str, code: str):
    """
    Optionnel : on tente de retrouver label/desc/penalty dans un fichier flags
    JSON ou CSV/TXT. Renvoie (name, desc, penalty_float) ou None.
    """
    p = Path(flags_path)
    if not p.exists():
        return None
    try:
        head = p.read_text(encoding="utf-8")[:4096]
    except Exception:
        return None

    # JSON {"headers","rows"} ou liste d'objets
    if head[:1] in ("{","["):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # liste d'objets de type {col:val}
                rows_raw = data
                headers = list(rows_raw[0].keys()) if rows_raw else []
                rows = [[r.get(h, "") for h in headers] for r in rows_raw]
            else:
                headers = (data or {}).get("headers") or []
                rows = (data or {}).get("rows") or []
            h = {str(h).strip().upper(): i for i, h in enumerate(headers)}
            for r in rows:
                def col(*names):
                    for nm in names:
                        j = h.get(nm)
                        if j is not None and j < len(r):
                            return r[j]
                    return ""
                r_code = str(col("CODE","FLAG ABBR","ABBR")).strip().upper()
                if r_code == code:
                    name = str(col("FLAG NAME","LABEL","NAME")).strip()
                    desc = str(col("FLAG DESCRIPTION","DESCRIPTION","DESC")).strip() or (name or f"Rule {code}.")
                    pen  = str(col("FLAG PENALTY__","FLAG PENALTY","PENALTY")).replace("%","").replace(",", ".").strip()
                    pen_v = float(pen)/100.0 if pen else None
                    return (name, desc, pen_v if pen_v is not None else default_penalty_for(code))
        except Exception:
            return None

    # CSV/TXT
    try:
        sample = p.read_text(encoding="utf-8", errors="ignore")
        rows = list(csv.reader(sample.splitlines()))
        if not rows:
            return None
        headers = [h.strip().upper() for h in rows[0]]
        def idx(*names):
            for n in names:
                if n in headers: return headers.index(n)
            return -1
        i_code = idx("CODE","FLAG ABBR","ABBR")
        if i_code < 0:
            return None
        i_name = idx("FLAG NAME","LABEL","NAME")
        i_desc = idx("FLAG DESCRIPTION","DESCRIPTION","DESC")
        i_pen  = idx("FLAG PENALTY__","FLAG PENALTY","PENALTY")
        for r in rows[1:]:
            if i_code >= len(r): continue
            if str(r[i_code]).strip().upper() == code:
                name = (r[i_name] if 0 <= i_name < len(r) else "").strip()
                desc = (r[i_desc] if 0 <= i_desc < len(r) else "").strip()
                pen  = (r[i_pen]  if 0 <= i_pen  < len(r) else "").strip().replace("%","").replace(",", ".")
                pen_v = float(pen)/100.0 if pen else None
                return (name, desc or name or f"Rule {code}.", pen_v if pen_v is not None else default_penalty_for(code))
    except Exception:
        return None
    return None

def render_for_code(template: str, subs: dict[str, str], src: Path) -> str:
    pat = re.compile(r"<<([A-Z0-9_]+)>>")
    def repl(m):
        key = m.group(1)
        if key not in subs:
            raise SystemExit(f"[ERR] {src.name}: No value for placeholder <<{key}>>")
        return subs[key]
    out = pat.sub(repl, template)
    leftovers = pat.findall(out)
    if leftovers:
        missing = ", ".join(sorted(set(leftovers)))
        raise SystemExit(f"[ERR] {src.name}: Unresolved placeholders: {missing}")
    return out

# --------------------------
# Join UTBMS sur les SAMPLES
# --------------------------
def _norm(s):
    if s is None: return ""
    return str(s).strip().strip('"').strip("'")

def _norm_code(s):
    t = _norm(s).upper().replace(" ", "")
    # garde un motif simple comme A101 / L350
    if len(t) >= 3 and t[0] in ("A","L") and t[1:].isalnum():
        return t
    return t

def _read_csv_text(text: str) -> List[Dict[str, str]]:
    if not text.strip():
        return []
    f = io.StringIO(text)
    rdr = csv.DictReader(f)
    return list(rdr)

def _write_csv_text(rows: List[Dict[str, str]], fieldnames: List[str]) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fieldnames)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in fieldnames})
    return out.getvalue().rstrip()

def _utbms_map_from_text(utbms_text: str) -> Dict[str, Dict[str, str]]:
    rows = _read_csv_text(utbms_text)
    if not rows:
        return {}
    # Colonnes mini: CATEGORY,SUBCATEGORY,CODE,DESCRIPTION
    mp = {}
    for r in rows:
        code = _norm_code(r.get("CODE"))
        if not code: 
            continue
        mp[code] = {
            "UTBMS_CATEGORY": _norm(r.get("CATEGORY")),
            "UTBMS_SUBCATEGORY": _norm(r.get("SUBCATEGORY")),
            "UTBMS_DESCRIPTION": _norm(r.get("DESCRIPTION")),
        }
    return mp

def _join_invoice_rows_with_utbms(inv_rows: List[Dict[str, str]], mp: Dict[str, Dict[str, str]]) -> List[Dict[str, str]]:
    out_rows: List[Dict[str, str]] = []
    extra = ["UTBMS_CODE","UTBMS_FAMILY","UTBMS_CATEGORY","UTBMS_SUBCATEGORY","UTBMS_DESCRIPTION"]
    for row in inv_rows:
        # calcule code et famille
        act = _norm_code(row.get("LINE_ITEM_ACTIVITY_CODE"))
        task = _norm_code(row.get("LINE_ITEM_TASK_CODE"))
        code = act or task
        family = "ACTIVITY" if act else ("TASK" if task else "")
        joined = dict(row)
        joined["UTBMS_CODE"] = code
        joined["UTBMS_FAMILY"] = family
        info = mp.get(code, {}) if code else {}
        joined["UTBMS_CATEGORY"] = info.get("UTBMS_CATEGORY","")
        joined["UTBMS_SUBCATEGORY"] = info.get("UTBMS_SUBCATEGORY","")
        joined["UTBMS_DESCRIPTION"] = info.get("UTBMS_DESCRIPTION","")
        out_rows.append(joined)
    # harmonise les colonnes (garde colonnes d'origine + extra)
    base_fields = list(inv_rows[0].keys()) if inv_rows else []
    fieldnames = base_fields[:]
    for col in extra:
        if col not in fieldnames:
            fieldnames.append(col)
    # normalise ordre pour tous
    normed = []
    for r in out_rows:
        for col in extra:
            r.setdefault(col, "")
        normed.append(r)
    return normed, fieldnames

def make_joined_invoice_sample(invoice_sample_text: str, utbms_sample_text: str) -> str:
    inv_rows = _read_csv_text(invoice_sample_text)
    utbms_map = _utbms_map_from_text(utbms_sample_text)
    if not inv_rows or not utbms_map:
        # si l'un est vide, on renvoie l'original tel quel (fallback)
        return invoice_sample_text
    joined_rows, fieldnames = _join_invoice_rows_with_utbms(inv_rows, utbms_map)
    return _write_csv_text(joined_rows, fieldnames)

# -----------
# Programme
# -----------
def main():
    template = read_text(TPL)
    paths = parse_paths_file(PATHS_TXT)

    # Charge SAMPLES (textuels)…
    base_subs = {ph: read_text(fp) for ph, fp in SAMPLES.items()}

    # …et remplace INVOICE_SAMPLE par une version JOINED avec UTBMS_SAMPLE
    try:
        joined_invoice_sample = make_joined_invoice_sample(
            base_subs.get("INVOICE_SAMPLE",""),
            base_subs.get("UTBMS_SAMPLE",""),
        )
        base_subs["INVOICE_SAMPLE"] = joined_invoice_sample
    except Exception as e:
        # En cas de problème de join sur samples, on garde l'original sans casser la génération
        print(f"[WARN] Could not join INVOICE_SAMPLE with UTBMS_SAMPLE: {e}", file=sys.stderr)

    # Injecte aussi les vrais chemins (utiles dans le prompt si tu les affiches)
    base_subs.update(paths)

    codes = parse_rule_codes(RULES_TXT)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for code in codes:
        info = load_flags_info(paths["FLAGS_PATH"], code)
        if info:
            _, description, penalty = info
        else:
            description = f"Rule {code}."
            penalty = default_penalty_for(code)

        subs = dict(base_subs)
        subs.update({
            "RULE_CODE": code,
            # ancienne clé (déjà utilisée par ton template)
            "RULE_DESCRIPTION_1_OR_2_SENTENCES": description,
            "DEFAULT_PENALTY_0_TO_1": f"{penalty:.6g}",
            "RULE_VERSION_INT": "1",
            # nouvelles clés pour le nouveau template
            "RULE_DESCRIPTION": description,
            "DEFAULT_PENALTY": f"{penalty:.6g}",
            "RULE_VERSION": "1",
            # nom de fonction
            "FUNCTION_NAME": function_name_for(code),
        })

        rendered = render_for_code(template, subs, TPL)
        out_path = OUT_DIR / f"final_prompt_{code}.txt"
        out_path.write_text(rendered, encoding="utf-8")
        print(f"[OK] {out_path.relative_to(ROOT)}  (penalty={penalty})")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
