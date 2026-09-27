from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal


def num(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def plain(x):
    """SDK response objects (frozendicts, Decimal, schema-wrapped bools/None, pydantic) -> plain JSON types."""
    if x is None or isinstance(x, bool):
        return x
    name = type(x).__name__
    if name == "NoneClass":
        return None
    if name == "BoolClass":
        return bool(x)
    if hasattr(x, "model_dump"):
        return plain(x.model_dump())
    if isinstance(x, Mapping):
        return {str(k): plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x]
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, int):
        return int(x)
    if isinstance(x, float):
        return float(x)
    if isinstance(x, str):
        return str(x)
    return str(x)
