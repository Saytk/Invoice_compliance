from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Set, Tuple


def load_required_fields_map(required_fields_path: Path) -> Dict[str, List[str]]:
    """
    Input: narrative/required_fields.json
    Output: dict { FLAG_CODE: [fields...] }
    """
    data = json.loads(required_fields_path.read_text(encoding="utf-8"))
    out: Dict[str, List[str]] = {}

    if not isinstance(data, dict):
        return out

    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, list):
            continue
        code = k.strip().upper()
        if not code:
            continue

        fields: List[str] = []
        for f in v:
            if isinstance(f, str):
                f = f.strip()
                if f:
                    fields.append(f)

        out[code] = fields

    return out


def split_csv_fields_and_context(fields: List[str]) -> Tuple[List[str], bool]:
    """
    ALL_LINES n'est pas une colonne CSV.
    Retourne (csv_fields, needs_all_lines).
    """
    needs_all = any((f or "").strip().upper() == "ALL_LINES" for f in fields)
    csv_fields = [f for f in fields if (f or "").strip().upper() != "ALL_LINES"]
    return csv_fields, needs_all


def union_required_csv_fields(
    required_map: Dict[str, List[str]],
    flag_codes: List[str],
) -> Tuple[List[str], Set[str], bool]:
    """
    Pour un set de flags, calcule:
    - champs CSV requis (union)
    - flags retenus (intersect avec required_map)
    - needs_all_lines (si au moins un flag demande ALL_LINES)
    """
    wanted = [c.strip().upper() for c in (flag_codes or []) if c and c.strip()]
    wanted_set = set(wanted)

    present = {c for c in wanted_set if c in required_map}
    csv_fields: Set[str] = set()
    needs_all = False

    for c in present:
        fields = required_map.get(c, [])
        csv_f, need = split_csv_fields_and_context(fields)
        csv_fields.update(csv_f)
        if need:
            needs_all = True

    return sorted(csv_fields), present, needs_all
