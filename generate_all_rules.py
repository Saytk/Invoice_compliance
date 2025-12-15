from __future__ import annotations
import subprocess
import sys
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parent

BUILD_PROMPTS = ROOT / "prompts_generator.py"
GENERATE_RULES = ROOT / "llm_generate_rule.py"


def run_step(name: str, script_path: Path, extra_args: list[str] | None = None) -> None:
    """
    Lance un script Python en sous-process.
    - name: label affiché dans la console
    - script_path: chemin du script à lancer
    - extra_args: liste d’arguments à passer au script (ex: ["--debug"])
    """
    print(f"\n=== [STEP] {name} ===")
    if not script_path.exists():
        print(f"[ERR] Script not found: {script_path}")
        sys.exit(1)

    cmd = [sys.executable, str(script_path)]
    if extra_args:
        cmd.extend(extra_args)

    print(f"[INFO] Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=False,
        text=True,
    )
    if result.returncode != 0:
        print(f"[ERR] {name} failed (exit={result.returncode})")
        sys.exit(result.returncode)

    print(f"[OK] {name} completed.")


def main():
    parser = argparse.ArgumentParser(
        description="Orchestrator: generate prompts and rule .py files."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode for llm_generate_rule.py (passes --debug).",
    )
    args = parser.parse_args()

    # 1) Générer les prompts
    run_step("Generate prompts", BUILD_PROMPTS)

    # 2) Générer les règles à partir des prompts
    extra = ["--debug"] if args.debug else None
    run_step("Generate rule functions", GENERATE_RULES, extra_args=extra)

    print("\n=== DONE ===")
    print("Prompts and rule functions generated successfully.")


if __name__ == "__main__":
    main()
