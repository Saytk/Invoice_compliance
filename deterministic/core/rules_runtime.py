from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Optional, Union


# ============================
# Core types
# ============================
@dataclass
class RuleResult:
    triggered: bool
    code: str
    penalty: float
    message: str
    score: Optional[float] = None
    meta: Optional[dict[str, Any]] = None
    error: Optional[str] = None


# ============================
# Decorator
# ============================
def rule(
    code: str,
    penalty: Union[int, float] = 0.0,
    desc: str = "",
    version: int = 1,
):
    """
    Decorator used in all auto-generated rule files.

    Typical usage in generated code:
      @rule("CMC", penalty=0.3333, desc="...", version=1)
      def rule_cmc(line) -> RuleResult | None:
          ...

    We store metadata on the function object:
      _rule_code, _rule_desc, _rule_version, _rule_penalty
    """

    pen = float(penalty)

    def decorator(func):
        func._rule_code = code
        func._rule_desc = desc
        func._rule_version = version
        func._rule_penalty = pen
        return func

    return decorator


# ============================
# Helpers used by rules
# ============================
def parse_date(s: str) -> date:
    """
    Parse dates like 'YYYYMMDD' or 'YYYY-MM-DD' (or generic ISO format).
    Returns a datetime.date.
    """
    if not s:
        raise ValueError("Empty date string")

    s = s.strip()

    # 20240131
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()

    # 2024-01-31 or similar ISO date/datetime
    try:
        # fromisoformat gère 'YYYY-MM-DD' et 'YYYY-MM-DDTHH:MM:SS'
        dt = datetime.fromisoformat(s)
        return dt.date()
    except Exception:
        # dernier fallback : essayer quelques formats classiques
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except Exception:
                continue

    raise ValueError(f"Unrecognized date format: {s!r}")


def days_between(d1: date, d2: date) -> int:
    """
    Renvoie (d2 - d1).days, positif si d2 > d1, négatif sinon.
    Les règles peuvent utiliser abs(days_between(...)) si besoin.
    """
    return (d2 - d1).days


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Conversion robuste d'un champ numérique (string, Decimal, float, int)
    vers float. Si échec ou None -> default.
    """
    if value is None:
        return default

    # Déjà un nombre
    if isinstance(value, (int, float)):
        return float(value)

    # Decimal
    if isinstance(value, Decimal):
        return float(value)

    # String ou autre
    s = str(value).strip()
    if not s:
        return default
    try:
        return float(s)
    except Exception:
        return default
