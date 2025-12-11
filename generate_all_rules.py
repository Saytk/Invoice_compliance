import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

BUILD_PROMPTS = ROOT / "build_prompts.py"
GENERATE_RULES = ROOT / "llm_generate_rule.py"

def run_step(name, script_path):
    print(f"\n=== [STEP] {name} ===")
    if not script_path.exists():
        print(f"[ERR] Script not found: {script_path}")
        sys.exit(1)

    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=False,
        text=True
    )
    if result.returncode != 0:
        print(f"[ERR] {name} failed (exit={result.returncode})")
        sys.exit(result.returncode)
    print(f"[OK] {name} completed.")

def main():
    run_step("Generate prompts", BUILD_PROMPTS)
    run_step("Generate rule functions", GENERATE_RULES)

    print("\n=== DONE ===")
    print("Prompts and rule functions generated successfully.")

if __name__ == "__main__":
    main()
