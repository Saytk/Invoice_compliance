# llm_generate_rule.py
from __future__ import annotations
from pathlib import Path
import os, re, sys, time, argparse

ROOT = Path(__file__).resolve().parent
PROMPTS_OUT = ROOT / "prompts" / "out"
RULES_OUT = ROOT / "rules_out"
LOGS_FAIL = ROOT / "logs_failed"
OPENAI_KEY_FILE = ROOT / "openai_key.txt"
RULES_TXT = ROOT / "prompts" / "rules" / "current_rules.txt"

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.1")


# -----------------------
# Helpers: key + OpenAI
# -----------------------
def read_api_key() -> str:
    if "OPENAI_API_KEY" in os.environ:
        return os.environ["OPENAI_API_KEY"].strip()
    if OPENAI_KEY_FILE.exists():
        return OPENAI_KEY_FILE.read_text(encoding="utf-8").strip()
    raise SystemExit("[ERR] Missing OpenAI API key (env OPENAI_API_KEY or openai_key.txt)")


def call_openai(prompt: str) -> str:
    import requests, json  # json pas nécessaire mais laissé tel quel

    api_key = read_api_key()
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # Système strict: un seul bloc, en ~~~python ... ~~~, pas d'import, commence par @rule(
    system_msg = (
        "You are a precise coding assistant. "
        "Return ONLY a single fenced Python code block delimited with '~~~python' and ending with '~~~'. "
        "No explanations, no prose before or after. "
        "The FIRST non-whitespace character in your code block MUST be '@rule('. "
        "Do NOT write any import statements. "
        "Do NOT use backticks in your response."
    )

    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    for attempt in range(5):
        resp = requests.post(url, headers=headers, json=body, timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        raise SystemExit(f"[ERR] OpenAI API error {resp.status_code}: {resp.text[:500]}")
    raise SystemExit("[ERR] OpenAI API retry limit reached")


# -----------------------
# Extraction & parsing
# -----------------------
def _largest_code_block(s: str) -> str | None:
    """
    Cherche d'abord des blocs ~~~python ... ~~~, puis ```python ... ``` et enfin ~~~ ... ~~~ / ``` ... ```.
    On prend le bloc le plus long.
    """
    # 1) priorité aux ~~~python
    blocks = re.findall(r"~~~python\s*(.*?)~~~", s, re.DOTALL | re.IGNORECASE)
    # 2) sinon, ```python (au cas où le modèle insiste)
    if not blocks:
        blocks = re.findall(r"```python\s*(.*?)```", s, re.DOTALL | re.IGNORECASE)
    # 3) sinon, ~~~ générique
    if not blocks:
        blocks = re.findall(r"~~~\s*(.*?)~~~", s, re.DOTALL)
    # 4) sinon, ``` générique
    if not blocks:
        blocks = re.findall(r"```\s*(.*?)```", s, re.DOTALL)
    if not blocks:
        return None
    return max(blocks, key=len).strip()


def _extract_decorated_function(code: str) -> str:
    """
    Coupe le texte à partir de la première occurrence de '@rule('.
    Si plusieurs fonctions décorées, garde la première (jusqu'à la prochaine occurrence \n@rule().
    """
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    m = re.search(r"^.*?@rule\(", code, flags=re.DOTALL | re.MULTILINE)
    if not m:
        return code.strip()
    start = m.start()
    sliced = code[start:].lstrip()

    nxt = re.search(r"\n@rule\(", sliced)
    if nxt:
        sliced = sliced[:nxt.start()].rstrip()
    return sliced.strip()


def _has_single_decorated_function(code: str) -> bool:
    deco_count = len(re.findall(r"^@rule\(", code, flags=re.MULTILINE))
    def_count = len(re.findall(r"^def\s+\w+\s*\(", code, flags=re.MULTILINE))
    return deco_count == 1 and def_count == 1


# -----------------------
# Sanitization + auto-imports
# -----------------------
def _sanitize_code(code: str) -> str:
    """
    Nettoie le bloc:
    - retire les délimiteurs ~~~python / ~~~ et les éventuels backticks ou ```python si présents
    - supprime toute ligne d'import (on gère nous-mêmes)
    - force penalty entier -> float dans le décorateur ET dans les appels RuleResult
    """
    code = code.replace("\r\n", "\n").replace("\r", "\n")

    # Enlève les délimiteurs éventuels
    code = code.replace("~~~python", "").replace("~~~", "")
    code = code.replace("```python", "").replace("```", "")

    code = code.strip()

    # Supprime imports (on veut centraliser)
    code = re.sub(r"^\s*import[^\n]*\n", "", code, flags=re.MULTILINE)
    code = re.sub(r"^\s*from\s+\w+(?:\.\w+)*\s+import[^\n]*\n", "", code, flags=re.MULTILINE)

    # penalty entier -> float dans le décorateur @rule(...)
    code = re.sub(r'(@rule\(\s*"[^"]+"\s*,\s*penalty=\s*)(\d+)(\s*,)', r"\1\2.0\3", code)

    # penalty entier -> float dans RuleResult(..., penalty, ...)
    code = re.sub(r'(RuleResult\(\s*True\s*,\s*"[^"]+"\s*,\s*)(\d+)(\s*,)', r"\1\2.0\3", code)

    return code.strip()


def _detect_needed_imports(code: str) -> list[str]:
    """
    Détecte l'usage de symboles autorisés et retourne les imports nécessaires.
    """
    imports: list[str] = []

    # datetime / date
    if re.search(r"\bdatetime\s*\.", code) or re.search(r"\bdatetime\s*\(", code) or re.search(r"\bdate\s*\(", code):
        if "from datetime import datetime, date" not in imports:
            imports.append("from datetime import datetime, date")

    # decimal.Decimal
    if re.search(r"\bDecimal\s*\(", code):
        if "from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN" not in imports:
            imports.append("from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN")

    # re
    if re.search(r"\bre\.", code):
        if "import re" not in imports:
            imports.append("import re")

    # collections
    if re.search(r"\bCounter\b", code):
        if "from collections import Counter" not in imports:
            imports.append("from collections import Counter")
    if re.search(r"\bdefaultdict\s*\(", code):
        if "from collections import defaultdict" not in imports:
            imports.append("from collections import defaultdict")

    # math
    if re.search(r"\bmath\.", code):
        if "import math" not in imports:
            imports.append("import math")

    # itertools
    if re.search(r"\bitertools\.", code) or re.search(r"\bgroupby\s*\(", code) or re.search(r"\bchain\s*\(", code):
        if "import itertools" not in imports:
            imports.append("import itertools")

    return imports


def _prepend_imports(imports: list[str], code: str) -> str:
    if not imports:
        return code
    header = "\n".join(imports).rstrip()
    return f"{header}\n\n{code.lstrip()}"


# -----------------------
# Logging
# -----------------------
def _log_failure(
    rule_code: str,
    reason: str,
    content: str,
    block: str | None = None,
    code_text: str | None = None,
    debug: bool = False,
):
    LOGS_FAIL.mkdir(parents=True, exist_ok=True)
    fp = LOGS_FAIL / f"log_fail_{rule_code}.txt"

    parts = [
        f"[FAIL] Rule {rule_code}",
        f"Reason: {reason}",
        "\n===== RAW CONTENT =====",
        (content or "").strip(),
    ]
    if block is not None:
        parts += ["\n===== LARGEST CODE BLOCK =====", block.strip()]
    if code_text is not None:
        parts += ["\n===== EXTRACTED CODE TEXT =====", code_text.strip()]

    text = "\n".join(parts) + "\n"
    fp.write_text(text, encoding="utf-8")
    print(f"[LOG] Saved failure content to {fp}")
    if debug:
        print(text)


# -----------------------
# Prompt + rules helpers
# -----------------------
def infer_names_from_prompt(prompt_text: str) -> tuple[str, str]:
    def grab(key: str) -> str:
        m = re.search(rf"{key}\s*:\s*['\"]?(.+?)['\"]?\s*$", prompt_text, re.MULTILINE)
        if not m:
            raise SystemExit(f"[ERR] Could not infer {key} from prompt")
        return m.group(1).strip().strip("<>").strip()

    rule_code = grab("RULE_CODE")
    func_name = grab("FUNCTION_NAME")
    return rule_code, func_name


def _read_text(p: Path) -> str:
    if not p.exists() or not p.is_file():
        raise SystemExit(f"[ERR] Missing file: {p}")
    return p.read_text(encoding="utf-8")


def parse_rule_codes(file_path: Path) -> list[str]:
    """
    Même logique que dans prompts_generator.parse_rule_codes,
    pour garder la source de vérité: current_rules.txt
    """
    raw = _read_text(file_path)
    codes: list[str] = []
    for i, ln in enumerate(raw.splitlines(), 1):
        s = ln.strip()
        if not s or s.startswith(("#", ";")):
            continue
        parts = [p.strip() for p in s.split(";") if p.strip()]
        for part in parts:
            m = re.search(r"([A-Za-z0-9_]{2,32})", part)
            if not m:
                raise SystemExit(f"[ERR] Invalid rule code on line {i} in {file_path.name}: {part}")
            codes.append(m.group(1).upper())
    # de-dupe en conservant l'ordre
    seen = set()
    uniq: list[str] = []
    for c in codes:
        if c not in seen:
            uniq.append(c)
            seen.add(c)
    if not uniq:
        raise SystemExit(f"[ERR] No rule codes found in {file_path}")
    return uniq


# -----------------------
# Main
# -----------------------
def main():
    parser = argparse.ArgumentParser(description="Generate rule functions from prompts.")
    parser.add_argument("--debug", action="store_true", help="Print raw response and extraction info on failures.")
    args = parser.parse_args()
    debug = args.debug

    # 1) Règles à générer = celles dans current_rules.txt
    wanted_codes = set(parse_rule_codes(RULES_TXT))

    RULES_OUT.mkdir(parents=True, exist_ok=True)
    files = sorted(PROMPTS_OUT.glob("final_prompt_*.txt"))
    if not files:
        raise SystemExit("[ERR] No prompts found in prompts/out/")

    for p in files:
        prompt = p.read_text(encoding="utf-8")

        # On lit le RULE_CODE dans le prompt pour savoir si on doit le traiter
        rule_code, func = infer_names_from_prompt(prompt)

        if rule_code not in wanted_codes:
            print(f"[SKIP] {rule_code} (not in {RULES_TXT.name})")
            continue

        print(f"[GEN] {rule_code} -> {func}")

        content = call_openai(prompt)
        block = _largest_code_block(content)

        if block is None:
            # Aucun bloc de type ~~~ ou ``` détecté : tentative d'extraction directe
            code_text = _extract_decorated_function(content)
            if "@rule(" not in code_text:
                _log_failure(rule_code, "No fenced code block AND '@rule(' not found in whole response", content, None, None, debug)
                continue
        else:
            code_text = _extract_decorated_function(block)
            if "@rule(" not in code_text:
                # Fallback: extraction dans toute la réponse brute
                fallback = _extract_decorated_function(content)
                if "@rule(" in fallback:
                    code_text = fallback
                else:
                    _log_failure(rule_code, "Decorator '@rule(' not found after extraction", content, block, code_text, debug)
                    continue

        # Sanitize & auto-imports
        code_text = _sanitize_code(code_text)
        imports = _detect_needed_imports(code_text)
        final_code = _prepend_imports(imports, code_text)

        # Validation stricte sur le code final (imports + fonction)
        if "@rule(" not in final_code:
            _log_failure(rule_code, "Decorator '@rule(' not present after sanitization", content, block, final_code, debug)
            continue
        if not _has_single_decorated_function(final_code):
            _log_failure(rule_code, "Expected exactly one decorated function", content, block, final_code, debug)
            continue

        target = RULES_OUT / f"{rule_code}__{func}.py"
        target.write_text(final_code.rstrip() + "\n", encoding="utf-8")
        print(f"[OK] {target.relative_to(ROOT)}")

    print("[DONE] All rules generated.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
