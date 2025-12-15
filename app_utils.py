from __future__ import annotations

import os
import re
import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# =========================
# File helpers
# =========================

def read_text(p: Path) -> str:
    if not p.exists() or not p.is_file():
        raise RuntimeError(f"Missing file: {p}")
    return p.read_text(encoding="utf-8").rstrip()


# =========================
# Placeholders  <<KEY>>
# =========================

_PLACEHOLDER_RE = re.compile(r"<<([A-Z0-9_]+)>>")

def render_placeholders(template: str, subs: Dict[str, str], *, src_name: str = "<template>") -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in subs:
            raise RuntimeError(f"{src_name}: No value for placeholder <<{key}>>")
        return subs[key]

    out = _PLACEHOLDER_RE.sub(repl, template)
    leftovers = _PLACEHOLDER_RE.findall(out)
    if leftovers:
        missing = ", ".join(sorted(set(leftovers)))
        raise RuntimeError(f"{src_name}: Unresolved placeholders: {missing}")
    return out


# =========================
# paths.txt loader
# =========================

def parse_paths_file(p: Path, required: set[str] | None = None) -> Dict[str, str]:
    content = read_text(p)
    out: Dict[str, str] = {}
    for i, raw in enumerate(content.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if "=" not in line:
            raise RuntimeError(f"Invalid line in {p.name} at #{i}: expected KEY=VALUE")
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        val = os.path.expanduser(os.path.expandvars(val))
        out[key] = val

    if required:
        missing = required - set(out.keys())
        if missing:
            raise RuntimeError(f"Missing keys in {p.name}: {', '.join(sorted(missing))}")
    return out


# =========================
# Rule codes parser (current_rules.txt)
# =========================

def parse_rule_codes_file(file_path: Path) -> List[str]:
    raw = read_text(file_path)
    codes: List[str] = []
    for i, ln in enumerate(raw.splitlines(), 1):
        s = ln.strip()
        if not s or s.startswith(("#", ";")):
            continue
        parts = [p.strip() for p in s.split(";") if p.strip()]
        for part in parts:
            m = re.search(r"([A-Za-z0-9_]{2,32})", part)
            if not m:
                raise RuntimeError(f"Invalid rule code on line {i} in {file_path.name}: {part}")
            codes.append(m.group(1).upper())

    seen = set()
    uniq: List[str] = []
    for c in codes:
        if c not in seen:
            uniq.append(c)
            seen.add(c)

    if not uniq:
        raise RuntimeError(f"No rule codes found in {file_path}")
    return uniq


# =========================
# Flags info loader (CSV/TXT or JSON)
# =========================

def default_penalty_for(code: str) -> float:
    # Tu as dit “on oublie les pénalités” côté narratif,
    # mais ton deterministic prompt en a encore besoin.
    m = {"ADM": 0.9, "DT": 0.0, "BB": 0.275, "VE": 0.3}
    return float(m.get(code.upper(), 0.5))


def load_flags_info(flags_path: str, code: str) -> Optional[tuple[str, str, float]]:
    """
    Tente de retrouver label/desc/penalty dans un fichier flags JSON ou CSV/TXT.
    Renvoie (name, desc, penalty_float) ou None.
    """
    p = Path(flags_path)
    if not p.exists():
        return None

    try:
        head = p.read_text(encoding="utf-8")[:4096]
    except Exception:
        return None

    # JSON {"headers","rows"} ou liste d'objets
    if head[:1] in ("{", "["):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                rows_raw = data
                headers = list(rows_raw[0].keys()) if rows_raw else []
                rows = [[r.get(h, "") for h in headers] for r in rows_raw]
            else:
                headers = (data or {}).get("headers") or []
                rows = (data or {}).get("rows") or []

            h = {str(hd).strip().upper(): i for i, hd in enumerate(headers)}

            def col(r: list, *names: str) -> str:
                for nm in names:
                    j = h.get(nm)
                    if j is not None and j < len(r):
                        return str(r[j])
                return ""

            for r in rows:
                r_code = col(r, "CODE", "FLAG ABBR", "ABBR").strip().upper()
                if r_code == code.upper():
                    name = col(r, "FLAG NAME", "LABEL", "NAME").strip()
                    desc = col(r, "FLAG DESCRIPTION", "DESCRIPTION", "DESC").strip() or (name or f"Rule {code}.")
                    pen = col(r, "FLAG PENALTY__", "FLAG PENALTY", "PENALTY").replace("%", "").replace(",", ".").strip()
                    pen_v = float(pen) / 100.0 if pen else None
                    return (name, desc, float(pen_v) if pen_v is not None else default_penalty_for(code))
        except Exception:
            return None

    # CSV/TXT
    try:
        sample = p.read_text(encoding="utf-8", errors="ignore")
        rows = list(csv.reader(sample.splitlines()))
        if not rows:
            return None
        headers = [h.strip().upper() for h in rows[0]]

        def idx(*names: str) -> int:
            for n in names:
                if n in headers:
                    return headers.index(n)
            return -1

        i_code = idx("CODE", "FLAG ABBR", "ABBR")
        if i_code < 0:
            return None
        i_name = idx("FLAG NAME", "LABEL", "NAME")
        i_desc = idx("FLAG DESCRIPTION", "DESCRIPTION", "DESC")
        i_pen = idx("FLAG PENALTY__", "FLAG PENALTY", "PENALTY")

        for r in rows[1:]:
            if i_code >= len(r):
                continue
            if str(r[i_code]).strip().upper() == code.upper():
                name = (r[i_name] if 0 <= i_name < len(r) else "").strip()
                desc = (r[i_desc] if 0 <= i_desc < len(r) else "").strip()
                pen = (r[i_pen] if 0 <= i_pen < len(r) else "").strip().replace("%", "").replace(",", ".")
                pen_v = float(pen) / 100.0 if pen else None
                return (name, desc or name or f"Rule {code}.", float(pen_v) if pen_v is not None else default_penalty_for(code))
    except Exception:
        return None

    return None


# =========================
# Small CSV helpers for sample-join UTBMS
# =========================

def _norm(s: Any) -> str:
    if s is None:
        return ""
    return str(s).strip().strip('"').strip("'")


def _norm_code(s: Any) -> str:
    t = _norm(s).upper().replace(" ", "")
    if len(t) >= 3 and t[0] in ("A", "L") and t[1:].isalnum():
        return t
    return t


def _read_csv_text(text: str) -> List[Dict[str, str]]:
    if not text.strip():
        return []
    import io
    f = io.StringIO(text)
    rdr = csv.DictReader(f)
    return list(rdr)


def _write_csv_text(rows: List[Dict[str, str]], fieldnames: List[str]) -> str:
    import io
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
    mp: Dict[str, Dict[str, str]] = {}
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


def make_joined_invoice_sample(invoice_sample_text: str, utbms_sample_text: str) -> str:
    inv_rows = _read_csv_text(invoice_sample_text)
    utbms_map = _utbms_map_from_text(utbms_sample_text)
    if not inv_rows or not utbms_map:
        return invoice_sample_text

    out_rows: List[Dict[str, str]] = []
    extra = ["UTBMS_CODE", "UTBMS_FAMILY", "UTBMS_CATEGORY", "UTBMS_SUBCATEGORY", "UTBMS_DESCRIPTION"]

    for row in inv_rows:
        act = _norm_code(row.get("LINE_ITEM_ACTIVITY_CODE"))
        task = _norm_code(row.get("LINE_ITEM_TASK_CODE"))
        code = act or task
        family = "ACTIVITY" if act else ("TASK" if task else "")
        joined = dict(row)
        joined["UTBMS_CODE"] = code
        joined["UTBMS_FAMILY"] = family
        info = utbms_map.get(code, {}) if code else {}
        joined["UTBMS_CATEGORY"] = info.get("UTBMS_CATEGORY", "")
        joined["UTBMS_SUBCATEGORY"] = info.get("UTBMS_SUBCATEGORY", "")
        joined["UTBMS_DESCRIPTION"] = info.get("UTBMS_DESCRIPTION", "")
        out_rows.append(joined)

    base_fields = list(inv_rows[0].keys())
    fieldnames = base_fields[:]
    for col in extra:
        if col not in fieldnames:
            fieldnames.append(col)

    for r in out_rows:
        for col in extra:
            r.setdefault(col, "")

    return _write_csv_text(out_rows, fieldnames)


# =========================
# OpenAI (/v1/responses) helpers
# =========================

OPENAI_URL = "https://api.openai.com/v1/responses"


def read_api_key(key_file: Optional[Path] = None) -> str:
    if "OPENAI_API_KEY" in os.environ and os.environ["OPENAI_API_KEY"].strip():
        return os.environ["OPENAI_API_KEY"].strip()
    if key_file and key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    raise RuntimeError("Missing OpenAI API key (env OPENAI_API_KEY or key file)")


def extract_output_text(resp_json: dict) -> str:
    chunks: List[str] = []
    for item in resp_json.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for c in item.get("content", []) or []:
            if isinstance(c, dict) and c.get("type") == "output_text":
                t = c.get("text", "")
                if isinstance(t, str) and t:
                    chunks.append(t)
    return "\n".join(chunks).strip()


def call_openai_responses_text(
    prompt: str,
    *,
    model: str,
    api_key_file: Optional[Path] = None,
    raw_log_path: Optional[Path] = None,
    timeout: int = 120,
    retries: int = 4,
) -> str:
    key = read_api_key(api_key_file)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    body = {
        "model": model,
        "input": prompt,
        "reasoning": {"effort": "none"},
        "text": {"verbosity": "low"},
    }

    last_err: Optional[str] = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(OPENAI_URL, headers=headers, json=body, timeout=timeout)
        except Exception as e:
            last_err = str(e)
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Network error calling OpenAI: {last_err}")

        if resp.status_code == 200:
            data = resp.json()
            if raw_log_path:
                try:
                    raw_log_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except Exception:
                    pass
            txt = extract_output_text(data)
            if not txt:
                raise RuntimeError("LLM returned empty output_text")
            return txt

        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(1.5 * (attempt + 1))
            continue

        raise RuntimeError(f"OpenAI API error {resp.status_code}: {resp.text[:500]}")

    raise RuntimeError("OpenAI API retry limit reached")


def call_openai_responses_json(
    prompt: str,
    *,
    model: str,
    api_key_file: Optional[Path] = None,
    raw_log_path: Optional[Path] = None,
    timeout: int = 120,
    retries: int = 4,
) -> Any:
    txt = call_openai_responses_text(
        prompt,
        model=model,
        api_key_file=api_key_file,
        raw_log_path=raw_log_path,
        timeout=timeout,
        retries=retries,
    )
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", txt, flags=re.S)
        if m:
            return json.loads(m.group(1))
        raise RuntimeError("LLM output is not valid JSON")
