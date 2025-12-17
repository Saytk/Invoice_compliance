from __future__ import annotations

import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import ClientError


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


_REASONING_RE = re.compile(r"^\s*<reasoning>.*?</reasoning>\s*", re.DOTALL)


def _strip_reasoning_tags(text: str) -> str:
    return _REASONING_RE.sub("", text or "").strip()


def _extract_text_from_openai_chat_completion(resp_obj: Any) -> Optional[str]:
    """
    Expected OpenAI-style chat completion:
    {"choices":[{"message":{"content":"..."}}], "usage": {...}}
    """
    if not isinstance(resp_obj, dict):
        return None

    v = resp_obj.get("output_text")
    if isinstance(v, str) and v.strip():
        return v

    choices = resp_obj.get("choices")
    if isinstance(choices, list) and choices:
        c0 = choices[0]
        if isinstance(c0, dict):
            msg = c0.get("message")
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content
    return None


def _try_parse_json_text(txt: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(txt, str) or not txt.strip():
        return None, "empty_text"
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict):
            return obj, None
        return None, "json_not_dict"
    except Exception as e:
        return None, f"json_parse_error: {type(e).__name__}: {e}"


def call_bedrock_json(
    *,
    prompt: str,
    model_id: str,
    raw_log_path: Optional[Path] = None,
    max_output_tokens: int = 1000,
    temperature: float = 0.0,
    debug_dir: Optional[Path] = None,
    call_index: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Returns (data_dict_or_none, usage_dict_or_none).
    Also dumps per-call response artifacts when debug_dir+call_index are provided.
    """
    rt = boto3.client("bedrock-runtime")

    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": int(max_output_tokens),
        "temperature": float(temperature),
    }

    def _dump_text(filename: str, text: str) -> None:
        if debug_dir is None or call_index is None:
            return
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / filename).write_text(text, encoding="utf-8")

    def _dump_json(filename: str, obj: Any) -> None:
        if debug_dir is None or call_index is None:
            return
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / filename).write_text(
            json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    try:
        resp = rt.invoke_model(
            modelId=model_id,
            body=json.dumps(payload).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )

        raw = resp["body"].read().decode("utf-8", errors="replace")

        # optional global JSONL raw log
        if raw_log_path is not None:
            _append_jsonl(raw_log_path, {"ts_utc": _utc(), "model_id": model_id, "raw": raw})

        # per-call raw dump
        if debug_dir is not None and call_index is not None:
            _dump_text(f"response_{call_index:04d}.raw.json", raw)

        # decode response JSON
        try:
            resp_obj = json.loads(raw)
        except Exception as e:
            err = {"stage": "raw_json_decode", "error": f"{type(e).__name__}: {e}"}
            _dump_json(f"response_{call_index:04d}.error.json", err)
            return None, None

        usage = resp_obj.get("usage") if isinstance(resp_obj, dict) else None
        if isinstance(usage, dict):
            _dump_json(f"response_{call_index:04d}.usage.json", usage)

        text = _extract_text_from_openai_chat_completion(resp_obj)
        if not text:
            err = {"stage": "extract_text", "error": "no_content_extracted", "keys": sorted(list(resp_obj.keys()))}
            _dump_json(f"response_{call_index:04d}.error.json", err)
            return None, usage if isinstance(usage, dict) else None

        text = _strip_reasoning_tags(text)
        _dump_text(f"response_{call_index:04d}.content.txt", text)

        data, perr = _try_parse_json_text(text)
        if perr:
            err = {"stage": "parse_json_content", "error": perr, "content_head": text[:900]}
            _dump_json(f"response_{call_index:04d}.error.json", err)
            return None, usage if isinstance(usage, dict) else None

        _dump_json(f"response_{call_index:04d}.parsed.json", data)
        return data, usage if isinstance(usage, dict) else None

    except ClientError as e:
        err = {"stage": "invoke_model", "error": str(e)}
        _dump_json(f"response_{call_index:04d}.error.json", err)
        return None, None
    except Exception:
        tb = traceback.format_exc()
        err = {"stage": "unknown", "error": tb}
        _dump_json(f"response_{call_index:04d}.error.json", err)
        return None, None
