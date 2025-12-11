from __future__ import annotations
import json
from pathlib import Path
import argparse
import sys


def infer_engine_category(flag: dict) -> str:
    """
    Retourne la catégorie moteur à partir des champs de classification.
    - NARRATIVE_LLM : tout ce qui est narratif
    - STRUCTURED    : non narratif mais déterministe avec les champs dispo
    - IGNORED_OR_PREPROCESS : le reste (trop flou, besoin externe, préproc, etc.)
    """
    if flag.get("is_narrative"):
        return "NARRATIVE_LLM"

    if flag.get("can_be_deterministic_with_available_fields"):
        return "STRUCTURED"

    return "IGNORED_OR_PREPROCESS"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Construit un mapping simple {code: engine_category} à partir du JSON de classification des flags."
    )
    parser.add_argument(
        "--input",
        type=str,
        default="data/flags_classification.json",
        help="Chemin du fichier JSON d'entrée (classification complète).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/flags_engine_categories.json",
        help="Chemin du fichier JSON de sortie (mapping simple code -> catégorie).",
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"[ERR] Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        raw = input_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as e:
        print(f"[ERR] Failed to read/parse JSON from {input_path}: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, list):
        print(f"[ERR] Expected a JSON array at top-level in {input_path}.", file=sys.stderr)
        sys.exit(1)

    mapping: dict[str, str] = {}
    counts = {
        "NARRATIVE_LLM": 0,
        "STRUCTURED": 0,
        "IGNORED_OR_PREPROCESS": 0,
    }

    for flag in data:
        code = flag.get("code")
        if not code:
            print("[WARN] Skipping flag without 'code' field:", flag, file=sys.stderr)
            continue

        category = infer_engine_category(flag)
        mapping[code] = category
        counts[category] += 1

    # Création du dossier de sortie si besoin
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"[OK] Wrote {len(mapping)} flags to {output_path}")
    print(
        "[STATS] STRUCTURED = {s}, NARRATIVE_LLM = {n}, IGNORED_OR_PREPROCESS = {i}".format(
            s=counts["STRUCTURED"],
            n=counts["NARRATIVE_LLM"],
            i=counts["IGNORED_OR_PREPROCESS"],
        )
    )


if __name__ == "__main__":
    main()
