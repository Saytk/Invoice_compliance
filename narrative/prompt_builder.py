from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Callable, Optional

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


def _pick_callable(mod: Any, names: List[str]) -> Optional[Callable[..., Any]]:
    for nm in names:
        fn = getattr(mod, nm, None)
        if callable(fn):
            return fn
    return None


def _available_callables(mod: Any) -> List[str]:
    out: List[str] = []
    for nm in dir(mod):
        if nm.startswith("_"):
            continue
        v = getattr(mod, nm)
        if callable(v):
            out.append(nm)
    return sorted(out)


# ============================================================
# Imports (project)
# ============================================================
from app_utils import call_openai_responses_json as call_openai_json  # matches your app_utils.py

import narrative.prompt_builder as pb  # <-- no "from ... import ..." to avoid ImportError

# ============================================================
# Config
# ============================================================
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.1")
RAW_LOG = str(PROJECT_ROOT / "narrative_llm_raw.json")

BATCH_LINES = int(os.environ.get("NARR_LLM_BATCH_LINES", "50"))
BATCH_FLAGS = int(os.environ.get("NARR_LLM_BATCH_FLAGS", "10"))

# Try to find the two functions we need in prompt_builder.py (whatever you named them)
BUILD_INPUTS_FN = _pick_callable(pb, [
    "build_prompt_inputs",
    "build_inputs",
    "make_prompt_inputs",
    "make_inputs",
])

RENDER_PROMPT_FN = _pick_callable(pb, [
    "render_narrative_prompt",
    "render_prompt",
    "build_prompt",
    "make_prompt",
    "render",
])


def run_narrative_grouped(
    *,
    lines: List[Dict[str, Any]],
    narrative_codes: List[str],
    flags_desc: Dict[str, str],
    required_fields_map: Dict[str, List[str]],
) -> Dict[str, List[List[str]]]:
    """
    Expected LLM output:
    {
      "BB":[["K1","K2"],["K3"]],
      "CMC":[["K7","K9"]]
    }
    """
    if not BUILD_INPUTS_FN or not RENDER_PROMPT_FN:
        avail = _available_callables(pb)
        raise RuntimeError(
            "prompt_builder.py is missing required functions.\n"
            "Need: one builder (build_prompt_inputs/build_inputs/make_prompt_inputs/...) "
            "and one renderer (render_narrative_prompt/render_prompt/build_prompt/...).\n"
            f"Found callables: {', '.join(avail) if avail else '(none)'}"
        )

    out: Dict[str, List[List[str]]] = {}
    if not lines or not narrative_codes:
        return out

    for lines_batch in chunk(lines, BATCH_LINES):
        for flags_batch in chunk(narrative_codes, BATCH_FLAGS):

            subs = BUILD_INPUTS_FN(  # type: ignore[misc]
                lines=lines_batch,
                flag_codes=flags_batch,
                flags_desc=flags_desc,
                required_fields_map=required_fields_map,
            )

            rendered = RENDER_PROMPT_FN(  # type: ignore[misc]
                subs=subs,
                prefix=f"NARR_{len(lines_batch)}L_{len(flags_batch)}F",
            )

            # renderer may return (prompt, meta) or just prompt
            if isinstance(rendered, tuple) and rendered:
                prompt = rendered[0]
            else:
                prompt = rendered

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
    print("narrative_llm.py loaded OK.")
    if not BUILD_INPUTS_FN or not RENDER_PROMPT_FN:
        print("prompt_builder callables:", ", ".join(_available_callables(pb)))
        raise SystemExit(2)
    print("Using builder:", BUILD_INPUTS_FN.__name__)
    print("Using renderer:", RENDER_PROMPT_FN.__name__)
