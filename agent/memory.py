"""Short-term query memory helpers for read-only finance tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from agent.planner import ToolCallPlan

_READ_TOOLS = {
    "finance_releves_search",
    "finance_releves_sum",
    "finance_releves_aggregate",
}
_PERIOD_KEYS = {"date_range", "month", "year"}
_SKIP_FILTER_KEYS = _PERIOD_KEYS | {"limit", "offset"}
_FOLLOWUP_KEYWORDS = {
    "et",
    "ok",
    "pareil",
    "idem",
    "dans",
    "en",
    "?",
}


@dataclass(slots=True)
class QueryMemory:
    """Persistable memory for the latest successful read query."""

    date_range: dict[str, str] | None = None
    month: str | None = None
    year: int | None = None
    filters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize memory to JSON-compatible dict."""

        result: dict[str, Any] = {"filters": dict(self.filters)}
        if self.date_range is not None:
            result["date_range"] = dict(self.date_range)
        if self.month is not None:
            result["month"] = self.month
        if self.year is not None:
            result["year"] = self.year
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> QueryMemory | None:
        """Deserialize persisted memory."""

        if not isinstance(raw, dict):
            return None

        filters = raw.get("filters")
        date_range = raw.get("date_range")
        month = raw.get("month")
        year = raw.get("year")

        return cls(
            date_range=_normalize_date_range(date_range),
            month=_normalize_month(month),
            year=_normalize_year(year),
            filters=_normalize_dict(filters) if isinstance(filters, dict) else {},
        )


def is_read_tool(tool_name: str) -> bool:
    """Return True when tool is a read-only query tool."""

    return tool_name in _READ_TOOLS


def is_followup_message(message: str) -> bool:
    """Heuristic for short follow-up messages."""

    normalized = " ".join(message.strip().lower().split())
    if not normalized:
        return False

    tokens = normalized.replace("?", " ? ").split()
    has_keyword = any(token in _FOLLOWUP_KEYWORDS for token in tokens)
    return has_keyword and len(tokens) <= 8


def extract_memory_from_plan(
    tool_name: str,
    payload: dict[str, object],
    meta: dict[str, object] | None = None,
) -> QueryMemory | None:
    """Build query memory from an executed read tool payload."""

    del meta
    if not is_read_tool(tool_name):
        return None

    normalized_payload = _normalize_dict(payload)
    if not normalized_payload:
        return None

    filters = {
        key: value
        for key, value in normalized_payload.items()
        if key not in _SKIP_FILTER_KEYS
    }

    return QueryMemory(
        date_range=_normalize_date_range(normalized_payload.get("date_range")),
        month=_normalize_month(normalized_payload.get("month")),
        year=_normalize_year(normalized_payload.get("year")),
        filters=filters,
    )


def apply_memory_to_plan(
    message: str,
    plan: ToolCallPlan,
    memory: QueryMemory | None,
) -> tuple[ToolCallPlan, str | None]:
    """Merge memory in a tool plan with priority explicit > payload > memory."""

    if memory is None or not is_read_tool(plan.tool_name):
        return plan, None

    payload = _normalize_dict(plan.payload)
    if not payload:
        return plan, None

    reason_parts: list[str] = []
    period_injected = False
    filter_injected = False
    has_period = any(key in payload for key in _PERIOD_KEYS)
    if not has_period:
        if memory.date_range is not None:
            payload["date_range"] = dict(memory.date_range)
            reason_parts.append("period_from_memory")
            period_injected = True
        elif memory.month is not None:
            payload["month"] = memory.month
            reason_parts.append("period_from_memory")
            period_injected = True
        elif memory.year is not None:
            payload["year"] = memory.year
            reason_parts.append("period_from_memory")
            period_injected = True

    if is_followup_message(message):
        filter_injected = _merge_missing_filters(payload, memory.filters)
        if filter_injected:
            reason_parts.append("followup_filters_from_memory")

    if not period_injected and not filter_injected:
        return plan, None

    updated_meta = dict(plan.meta)
    if reason_parts:
        updated_meta["memory_reason"] = ",".join(reason_parts)

    return (
        ToolCallPlan(
            tool_name=plan.tool_name,
            payload=payload,
            user_reply=plan.user_reply,
            meta=updated_meta,
        ),
        updated_meta.get("memory_reason") if reason_parts else None,
    )


def _merge_missing_filters(
    payload: dict[str, Any],
    memory_filters: dict[str, Any],
) -> bool:
    injected = False
    for key, value in memory_filters.items():
        if key in _PERIOD_KEYS:
            continue
        if key in {"merchant", "search"} and (
            "merchant" in payload or "search" in payload
        ):
            continue
        if key == "filters" and isinstance(value, dict):
            existing = payload.get("filters")
            if not isinstance(existing, dict):
                payload["filters"] = dict(value)
                injected = True
                continue
            for nested_key, nested_value in value.items():
                if nested_key not in existing:
                    existing[nested_key] = nested_value
                    injected = True
            continue
        if key not in payload:
            payload[key] = value
            injected = True
    return injected


def _normalize_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        clean_key = key.strip()
        if not clean_key:
            continue
        normalized[clean_key] = _normalize_value(value)
    return normalized


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, dict):
        return _normalize_dict(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value


def _normalize_date_range(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    start = raw.get("start_date")
    end = raw.get("end_date")
    start_norm = _normalize_date_token(start)
    end_norm = _normalize_date_token(end)
    if start_norm is None or end_norm is None:
        return None
    return {"start_date": start_norm, "end_date": end_norm}


def _normalize_date_token(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalize_month(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalize_year(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
