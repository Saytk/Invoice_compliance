from pathlib import Path
import sys


IGNORED_DIRS = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    "logs",
    "logs_failed",
    "rules_out",
    "artifacts",  # skip generated outputs by default
}

IGNORED_PATHS = {
    "tests/artifacts/generated",
    "tests/artifacts/raw_llm_logs",
}

IGNORED_EXTENSIONS = {".pyc", ".pyo", ".log"}
IGNORED_FILES = {".DS_Store"}


def should_skip(entry: Path, root: Path) -> bool:
    rel = entry.relative_to(root).as_posix()
    if entry.name in IGNORED_DIRS and entry.is_dir():
        return True
    if rel in IGNORED_PATHS:
        return True
    if entry.suffix in IGNORED_EXTENSIONS:
        return True
    if entry.name in IGNORED_FILES:
        return True
    return False


def format_tree(base: Path, root: Path, prefix: str = "") -> list[str]:
    entries = [
        e for e in sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        if not should_skip(e, root)
    ]
    lines: list[str] = []
    for index, entry in enumerate(entries):
        connector = "`-- " if index == len(entries) - 1 else "|-- "
        suffix = "/" if entry.is_dir() else ""
        lines.append(f"{prefix}{connector}{entry.name}{suffix}")
        if entry.is_dir():
            extension = "    " if index == len(entries) - 1 else "|   "
            lines.extend(format_tree(entry, root, prefix + extension))
    return lines


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "[binary or non-utf8 content omitted]"


def collect_targets(root: Path) -> dict[str, str]:
    targets: dict[str, str] = {}
    candidate_map = {
        "rules_client": [Path("deterministic/cli/rules_client.py")],
        "rules_engine": [Path("deterministic/core/rules_engine.py")],
        "rules_runtime": [Path("deterministic/core/rules_runtime.py")],
        "global_prompt_minimal": [Path("deterministic/resources/prompts/global_prompt_minimal.txt")],
        "rules_test": [Path("tests")],
        "generate_tests_from_rules": [
            Path("tests/cli/generate_rule_tests.py"),
            Path("tests/resources/prompts/generate_tests_from_rule.txt"),
            Path("tests/cli/build_test_prompt.py"),
        ],
        "llm_generate_rules": [Path("deterministic/cli/llm_generate_rule.py")],
        "pormptbuilder": [Path("deterministic/cli/prompts_generator.py")],
        "narrative_llm": [Path("narrative/core/narrative_llm.py")],
        "narrative_prompt_builder": [Path("narrative/core/prompt_builder.py")],
        "narrative_required_fields": [
            Path("narrative/core/required_fields.py"),
            Path("narrative/resources/required_fields.json"),
        ],
        "narrative_prompts": [Path("narrative/resources/prompts")],
    }

    for label, candidates in candidate_map.items():
        for candidate in candidates:
            full_path = root / candidate
            if full_path.exists():
                targets[label] = str(full_path)
                break
    return targets


def build_prompt() -> str:
    root = Path(__file__).resolve().parent
    lines: list[str] = []

    lines.append("== PROJECT ARCHITECTURE ==")
    lines.append(".")
    lines.extend(format_tree(root, root))
    lines.append("")

    targets = collect_targets(root)
    for label, path_str in targets.items():
        path = Path(path_str)
        lines.append(f"== {label} == {path.relative_to(root)}")
        if path.is_dir():
            lines.append("[directory contents]")
            lines.extend(format_tree(path, root, prefix=""))
        else:
            lines.append(read_text_file(path))
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    prompt = build_prompt()
    root = Path(__file__).resolve().parent
    output_path = root / "project_prompt.txt"
    output_path.write_text(prompt, encoding="utf-8")
    try:
        print(prompt)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(prompt.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()
