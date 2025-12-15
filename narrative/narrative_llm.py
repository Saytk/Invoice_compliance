from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

# ============================================================
# PYTHONPATH FIX — allow running this file directly
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================
# Local helpers (avoid missing imports from app_utils)
# ============================================================
def chunk(items: List[Any], n: int) -> Iterable[List[Any]]:
    if n <= 0:
        n = 1
    for i in range(0, len(items), n):
        yield items[i : i + n]

# ============================================================
# Imports
# ============================================================
from app_utils import call_openai_responses_json as call_openai_json  # fallback name
from narrative.prompt_builder import build_prompt_inputs, render_narrative_prompt

# ============================================================
# Config
# ============================================================
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.1")
RAW_LOG = str(PROJECT_ROOT / "narrative_llm_raw.json")

BATCH_LINES = int(os.environ.get("NARR_LLM_BATCH_LINES", "50"))
BATCH_FLAGS = int(os.environ.get("NARR_LLM_BATCH_FLAGS", "10"))


def run_narrative_grouped(
    *,
    lines: List[Dict[str, Any]],
    narrative_codes: List[str],
    flags_desc: Dict[str, str],
    required_fields_map: Dict[str, List[str]],
) -> Dict[str, List[List[str]]]:
    """
    Output format:
    {
      "BB":[["K1","K2"],["K3"]],
      "CMC":[["K7","K9"]]
    }
    """
    out: Dict[str, List[List[str]]] = {}

    if not lines or not narrative_codes:
        return out

    for lines_batch in chunk(lines, BATCH_LINES):
        for flags_batch in chunk(narrative_codes, BATCH_FLAGS):

            subs = build_prompt_inputs(
                lines=lines_batch,
                flag_codes=flags_batch,
                flags_desc=flags_desc,
                required_fields_map=required_fields_map,
            )

            prompt, _meta = render_narrative_prompt(
                subs=subs,
                prefix=f"NARR_{len(lines_batch)}L_{len(flags_batch)}F",
            )

            data = call_openai_json(
                prompt=prompt,
                model=MODEL,
                raw_log_path=Path(RAW_LOG),
            ) or {}

            if not isinstance(data, dict):
                continue

            for flag_code, groups in data.items():
                if not isinstance(groups, list):
                    continue
                cur = out.setdefault(str(flag_code), [])

                for g in groups:
                    if isinstance(g, list) and g:
                        cleaned = [k for k in g if isinstance(k, str) and k]
                        if cleaned:
                            cur.append(cleaned)

    return {k: v for k, v in out.items() if v}


if __name__ == "__main__":
    print("narrative_llm.py OK (imports + chunk).")
