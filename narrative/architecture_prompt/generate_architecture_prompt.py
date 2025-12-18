from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Tuple


NARRATIVE_ROOT = Path(__file__).resolve().parents[1]
THIS_DIR = Path(__file__).resolve().parent
INTRO_PATH = THIS_DIR / "intro.txt"
OUT_PATH = THIS_DIR / "narrative_architecture_prompt.txt"
REPO_ROOT = NARRATIVE_ROOT.parent

REL_FILES = [
    # Narrative core + resources
    "narrative/__init__.py",
    "narrative/architecture_prompt/generate_architecture_prompt.py",
    "narrative/architecture_prompt/intro.txt",
    "narrative/artifacts/sample_input/invoices_to_check.csv",
    "narrative/core/__init__.py",
    "narrative/core/narrative_heuristic",
    "narrative/core/narrative_llm.py",
    "narrative/core/prompt_builder.py",
    "narrative/core/required_fields.py",
    "narrative/core/schema.py",
    "narrative/resources/__init__.py",
    "narrative/resources/prompts/narrative_batch_csv.txt",
    "narrative/resources/required_fields.json",
    # Top-level helpers
    "app_utils.py",
    "app_utils_bedrock.py",
]

DESC_MAP: Dict[str, str] = {
    "narrative/__init__.py": "Package marker for narrative.",
    "narrative/artifacts/__init__.py": "Package marker for artifacts.",
    "narrative/artifacts/sample_input/invoices_to_check.csv": "Sample invoice CSV used for prompt tests.",
    "narrative/core/__init__.py": "Package marker for core logic.",
    "narrative/core/schema.py": "Placeholder for future schema definitions (empty).",
    "narrative/core/prompt_builder.py": "CLI/helpers that render the narrative prompt from CSV lines, flag codes, and required fields.",
    "narrative/core/narrative_llm.py": "Batches lines/flags, renders prompts, calls app_utils.call_openai_responses_json, and aggregates KIDs per flag.",
    "narrative/core/required_fields.py": "Loads required_fields.json, splits CSV vs ALL_LINES, computes union for selected flags.",
    "narrative/resources/__init__.py": "Package marker for resources.",
    "narrative/resources/required_fields.json": "Flag code -> required CSV columns mapping.",
    "narrative/resources/prompts/narrative_batch_csv.txt": "Template with LLM instructions to group KIDs by flag from CSV.",
    "narrative/architecture_prompt/generate_architecture_prompt.py": "Generator for this prompt (structure + contents).",
    "narrative/architecture_prompt/intro.txt": "Intro utilisateur a placer en tete du prompt final.",
    "narrative/core/narrative_heuristic": "Local embedding heuristic demo (percentage scoring with E5).",
    "app_utils.py": "OpenAI helper (call_openai_responses_json, parsing, logging).",
    "app_utils_bedrock.py": "Bedrock helper (clients, invocation, model routing).",
}

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_SUFFIXES = {".pyc"}
EXCLUDE_FILES = {OUT_PATH}


def iter_files() -> Iterable[Path]:
    for rel in REL_FILES:
        p = REPO_ROOT / rel
        if not p.exists() or p.is_dir():
            continue
        if p.suffix in EXCLUDE_SUFFIXES:
            continue
        if p in EXCLUDE_FILES:
            continue
        yield p


def short_desc(rel_path: str) -> str:
    return DESC_MAP.get(rel_path, "Fichier inclus dans narrative (contenu ci-dessous).")


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def build_prompt() -> str:
    intro = INTRO_PATH.read_text(encoding="utf-8", errors="replace").strip()

    entries: List[Tuple[str, str, str]] = []
    for p in iter_files():
        rel = p.relative_to(NARRATIVE_ROOT.parent).as_posix()
        entries.append((rel, short_desc(rel), read_file(p)))

    lines = [intro, "", "------------------------------------------------------------", "ARCHITECTURE NARRATIVE", "------------------------------------------------------------", "", "Structure (fichiers + role court):"]
    for rel, desc, _content in entries:
        lines.append(f"- {rel}: {desc}")

    lines.append("")
    lines.append("Contenu des fichiers (ordre alphabetique):")
    for rel, _desc, content in entries:
        lines.append(f"=== {rel} ===")
        lines.append(content.rstrip())
        lines.append("")

    lines.append("Notes: prompt_builder.py depends on app_utils.call_openai_responses_json; sample data lives in artifacts/.")
    return "\n".join(lines)


def main() -> int:
    THIS_DIR.mkdir(parents=True, exist_ok=True)

    OUT_PATH.write_text(build_prompt(), encoding="utf-8")
    print(f"[OK] Prompt written to: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
