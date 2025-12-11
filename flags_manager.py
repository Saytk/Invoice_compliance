# flags_manager.py
from __future__ import annotations
from pathlib import Path
import os
import sys
import json
import csv
import re
import time
import argparse
import requests

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
FLAGS_CSV = DATA_DIR / "flags.txt"          # <-- adapte si besoin
FLAGS_JSON = DATA_DIR / "flags_classified.json"
LLM_RAW_LOG = ROOT / "flags_llm_raw.json"

PROMPT_FILE = ROOT / "prompts" / "flags_classification_prompt.txt"

OPENAI_KEY_FILE = ROOT / "openai_key.txt"
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
    """
    Appelle l'endpoint /v1/responses de la nouvelle API OpenAI
    et renvoie le texte concaténé de tous les blocs 'output_text'.
    """
    api_key = read_api_key()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    body = {
        "model": MODEL,
        "input": prompt,
        # Pas de temperature/top_p/logprobs avec gpt-5.1 + reasoning none,
        # on laisse les valeurs par défaut.
        "reasoning": {"effort": "none"},
        "text": {"verbosity": "low"},
    }

    for attempt in range(5):
        try:
            resp = requests.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json=body,
                timeout=120,
            )
        except requests.exceptions.RequestException as e:
            # Erreur réseau -> retry
            if attempt < 4:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise SystemExit(f"[ERR] Network error calling OpenAI: {e}")

        if resp.status_code == 200:
            data = resp.json()

            # Log brut pour debug
            try:
                LLM_RAW_LOG.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception:
                pass

            # On récupère tous les 'output_text'
            output = data.get("output", [])
            text_chunks: list[str] = []

            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "message":
                    continue
                contents = item.get("content", []) or []
                for c in contents:
                    if not isinstance(c, dict):
                        continue
                    # selon la doc, type == "output_text"
                    if c.get("type") == "output_text":
                        txt = c.get("text", "")
                        if isinstance(txt, str):
                            text_chunks.append(txt)

            full_text = "\n".join(text_chunks).strip()
            if not full_text:
                raise SystemExit("[ERR] LLM returned no output_text content (see flags_llm_raw.json).")

            return full_text

        # 4xx/5xx -> retry sur 429/5xx temporaires
        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt < 4:
                time.sleep(1.5 * (attempt + 1))
                continue
        # autre erreur -> stop
        raise SystemExit(f"[ERR] OpenAI API error {resp.status_code}: {resp.text[:500]}")

    raise SystemExit("[ERR] OpenAI API retry limit reached")


# -----------------------
# Lecture des flags
# -----------------------
def load_flags_from_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"[ERR] Flags CSV not found: {path}")
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            # On normalise les noms de colonnes attendus
            rows.append({
                "GROUP NAME": r.get("GROUP NAME", "").strip(),
                "FLAG ABBR": r.get("FLAG ABBR", "").strip(),
                "FLAG NAME": r.get("FLAG NAME", "").strip(),
                "FLAG DESCRIPTION": r.get("FLAG DESCRIPTION", "").strip(),
                "FLAG PENALTY__": r.get("FLAG PENALTY__", "").strip(),
            })
    return rows


def build_flags_sample(flags: list[dict]) -> str:
    """
    On reconstruit un petit CSV inline à injecter dans <<FLAGS_SAMPLE>>.
    """
    out = ["GROUP NAME,FLAG ABBR,FLAG NAME,FLAG DESCRIPTION,FLAG PENALTY__"]
    for r in flags:
        def esc(s: str) -> str:
            s = s.replace('"', '""')
            if "," in s or '"' in s:
                return f'"{s}"'
            return s

        line = ",".join([
            esc(r["GROUP NAME"]),
            esc(r["FLAG ABBR"]),
            esc(r["FLAG NAME"]),
            esc(r["FLAG DESCRIPTION"]),
            esc(r["FLAG PENALTY__"]),
        ])
        out.append(line)
    return "\n".join(out)


# -----------------------
# Prompt builder
# -----------------------
def load_prompt_template() -> str:
    if not PROMPT_FILE.exists():
        raise SystemExit(f"[ERR] Prompt file not found: {PROMPT_FILE}")
    return PROMPT_FILE.read_text(encoding="utf-8")


def build_prompt(flags: list[dict]) -> str:
    tpl = load_prompt_template()
    sample_csv = build_flags_sample(flags)
    return tpl.replace("<<FLAGS_SAMPLE>>", sample_csv)


# -----------------------
# Main
# -----------------------
def main():
    parser = argparse.ArgumentParser(description="Classify billing flags as narrative/deterministic/etc.")
    args = parser.parse_args()

    print("[INFO] Loading flags…")
    flags = load_flags_from_csv(FLAGS_CSV)
    print(f"[INFO] {len(flags)} flags to classify via LLM.")

    print("[INFO] Reading prompt file…")
    prompt = build_prompt(flags)

    print("[INFO] Calling GPT-5.1 to classify flags…")
    raw_text = call_openai(prompt)

    # On essaie de parser le texte en JSON
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        # On logue le texte pour inspection
        LLM_RAW_LOG.write_text(raw_text, encoding="utf-8")
        raise SystemExit(f"[ERR] LLM response is not valid JSON: {e}")

    if not isinstance(data, list):
        LLM_RAW_LOG.write_text(raw_text, encoding="utf-8")
        raise SystemExit("[ERR] LLM response is not a JSON array.")

    # Validation minimale de la structure
    for obj in data:
        if not isinstance(obj, dict):
            raise SystemExit("[ERR] LLM response contains non-object elements.")
        for key in ("code", "group", "name", "description", "penalty_raw"):
            if key not in obj:
                raise SystemExit(f"[ERR] Missing key '{key}' in one of the flags.")

    # Sauvegarde
    FLAGS_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[OK] Classified flags written to {FLAGS_JSON}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
