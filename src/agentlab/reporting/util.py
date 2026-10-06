"""Small helpers shared by the report builder and the renderers."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

from agentlab.security.redactor import redact

CATEGORY_LABELS: dict[str, str] = {
    "functional_quality": "Functional quality",
    "instruction_adherence": "Instruction adherence",
    "conversational": "Conversation",
    "rag_quality": "RAG quality",
    "tool_use": "Tool use",
    "memory": "Memory",
    "planning": "Planning",
    "browser_execution": "Browser execution",
    "coding": "Coding",
    "multi_agent": "Multi-agent",
    "mcp": "MCP",
    "document": "Documents and multimodal",
    "reliability": "Reliability",
    "performance": "Performance",
    "cost_efficiency": "Cost efficiency",
    "security": "Security",
    "safety": "Safety",
}

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
SEVERITY_POINTS = {"critical": 100, "high": 70, "medium": 40, "low": 15, "info": 3}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category.replace("_", " ").capitalize())


def clean(text: Any, limit: int = 600) -> str:
    """Text from a target, a test or a judge as it may appear in a report: secrets redacted, control characters
    removed and the length bounded (the full redacted artifact is referenced by id)."""
    if text is None:
        return ""
    value = redact(str(text))
    value = _CONTROL.sub("", value if isinstance(value, str) else str(value))
    value = value.strip()
    if len(value) > limit:
        value = value[: max(limit - 1, 0)].rstrip() + "…"
    return value


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100); ``None`` for no data."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def money(value: float, known: bool = True) -> str:
    if not known:
        return "not reported"
    if value and value < 0.01:
        return f"${value:.5f}"
    return f"${value:.2f}"


def ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value / 1000:.2f} s" if value >= 1000 else f"{value:.0f} ms"


def sev_rank(severity: str | None) -> int:
    return len(SEVERITY_ORDER) - SEVERITY_ORDER.index(severity) if severity in SEVERITY_ORDER else 0


def histogram(values: Sequence[float], edges: Sequence[float]) -> list[dict[str, Any]]:
    """Counts per bucket ``[edges[i], edges[i+1])`` with a final open bucket."""
    out: list[dict[str, Any]] = []
    bounds = list(edges)
    for i, lo in enumerate(bounds):
        hi = bounds[i + 1] if i + 1 < len(bounds) else None
        count = sum(1 for v in values if v >= lo and (hi is None or v < hi))
        name = f"{lo:g}–{hi:g} ms" if hi is not None else f"≥ {lo:g} ms"
        out.append({"bucket": name, "from_ms": lo, "to_ms": hi, "count": count})
    return out


def describe_values(values: dict[str, Any]) -> str:
    """A review's ``original`` / ``reviewed`` values in one line: ``status failed · score 0.00 · severity high``."""
    parts: list[str] = []
    for key in ("status", "score", "severity"):
        if key in values and values[key] is not None:
            val = values[key]
            parts.append(f"{key} {val:.2f}" if isinstance(val, float) else f"{key} {val}")
    return " · ".join(parts) or "-"
