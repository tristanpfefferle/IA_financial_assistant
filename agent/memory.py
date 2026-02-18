"""Short-term query memory helpers for read-only finance tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import re
import unicodedata
from typing import Any

from agent.planner import ClarificationPlan, ToolCallPlan

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
    "?",
}
_FOLLOWUP_START_PATTERN = re.compile(r"^(?:ok\s+)?(?:et|pareil|idem)\b")
_FOLLOWUP_EXPLICIT_INTENT_PATTERN = re.compile(r"^(?:ok\s+)?et\b")
_INTENT_KEYWORDS = {
    "depense",
    "depenses",
    "dépense",
    "dépenses",
    "total",
    "totaux",
    "transaction",
    "transactions",
    "revenu",
    "revenus",
    "solde",
    "soldes",
    "liste",
    "lister",
    "categorie",
    "categories",
    "catégorie",
    "catégories",
    "agrege",
    "agrège",
    "agregat",
    "agrégat",
    "somme",
}
_NON_FOCUS_MESSAGES = {
    "ok",
    "oui",
    "non",
    "merci",
    "daccord",
    "d'accord",
}
_FOLLOWUP_STOP_TOKENS = {
    "liste",
    "lister",
    "supprime",
    "supprimer",
    "cree",
    "creer",
    "renomme",
    "modifier",
    "modifie",
    "categorie",
    "categories",
    "profil",
}
_MONTH_LOOKUP = {
    "janvier": 1,
    "janv": 1,
    "fevrier": 2,
    "février": 2,
    "fevr": 2,
    "févr": 2,
    "mars": 3,
    "avril": 4,
    "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "juil": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "sept": 9,
    "octobre": 10,
    "oct": 10,
    "novembre": 11,
    "nov": 11,
    "decembre": 12,
    "décembre": 12,
    "dec": 12,
    "déc": 12,
}
_INTENT_BY_TOOL = {
    "finance_releves_sum": "sum",
    "finance_releves_search": "search",
    "finance_releves_aggregate": "aggregate",
}
_RELEVES_TOOLS = frozenset(_INTENT_BY_TOOL.keys())


@dataclass(slots=True)
class QueryMemory:
    """Persistable memory for the latest successful read query."""

    date_range: dict[str, str] | None = None
    month: str | None = None
    year: int | None = None
    last_tool_name: str | None = None
    last_intent: str | None = None
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
        if self.last_tool_name is not None:
            result["last_tool_name"] = self.last_tool_name
        if self.last_intent is not None:
            result["last_intent"] = self.last_intent
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
        last_tool_name = raw.get("last_tool_name")
        last_intent = raw.get("last_intent")

        return cls(
            date_range=_normalize_date_range(date_range),
            month=_normalize_month(month),
            year=_normalize_year(year),
            last_tool_name=_normalize_string(last_tool_name),
            last_intent=_normalize_string(last_intent),
            filters=_normalize_dict(filters) if isinstance(filters, dict) else {},
        )


def is_read_tool(tool_name: str) -> bool:
    """Return True when tool is a read-only query tool."""

    return tool_name in _READ_TOOLS


def is_followup_message(message: str) -> bool:
    """Heuristic for short follow-up messages."""

    normalized = _normalize_text(message)
    if not normalized:
        return False

    if any(keyword in normalized.split() for keyword in _INTENT_KEYWORDS):
        return bool(_FOLLOWUP_EXPLICIT_INTENT_PATTERN.match(normalized))

    tokens = normalized.replace("?", " ? ").split()
    if len(tokens) <= 2:
        return True

    if _FOLLOWUP_START_PATTERN.match(normalized):
        return True

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
    category_candidate = filters.get("categorie")
    if isinstance(category_candidate, str) and _looks_like_period_phrase(category_candidate):
        filters.pop("categorie", None)

    return QueryMemory(
        date_range=_normalize_date_range(normalized_payload.get("date_range")),
        month=_normalize_month(normalized_payload.get("month")),
        year=_normalize_year(normalized_payload.get("year")),
        last_tool_name=tool_name,
        last_intent=_INTENT_BY_TOOL.get(tool_name),
        filters=filters,
    )


def followup_plan_from_message(
    message: str,
    memory: QueryMemory | None,
    *,
    known_categories: list[str] | None = None,
) -> ToolCallPlan | ClarificationPlan | None:
    """Build deterministic follow-up plan from short messages and memory."""

    if memory is None or not isinstance(memory.last_tool_name, str):
        return None
    if memory.last_tool_name not in _RELEVES_TOOLS:
        return None

    period_followup_plan = _build_period_change_followup_plan(message, memory)
    if period_followup_plan is not None:
        return period_followup_plan

    merchant_focus = _extract_merchant_focus(message)
    focus = _extract_followup_focus(message)
    category_focus = _known_category_in_message(message, known_categories or [])
    if focus is None and category_focus is None:
        if merchant_focus is None:
            return None
    if focus is None and category_focus is not None:
        focus = category_focus

    explicit_period_payload = _period_payload_from_message(message)
    period_payload = explicit_period_payload or _period_payload_from_memory(memory)
    normalized_focus = _normalize_text(focus) if isinstance(focus, str) else ""
    category = (
        _match_known_category(focus, known_categories or []) if isinstance(focus, str) else None
    )

    if merchant_focus is not None:
        normalized_merchant = _normalize_text(merchant_focus)
        if not normalized_merchant:
            return None
        if memory.last_tool_name == "finance_releves_search":
            payload = {
                "merchant": normalized_merchant,
                "limit": 50,
                "offset": 0,
                **period_payload,
            }
            return ToolCallPlan(
                tool_name="finance_releves_search",
                payload=payload,
                user_reply="OK.",
                meta={
                    "followup_from_memory": True,
                    "followup_focus": merchant_focus,
                    "followup_reason": "merchant_followup",
                    "source": "followup",
                },
            )

        payload = {
            "direction": "DEBIT_ONLY",
            "merchant": normalized_merchant,
            **period_payload,
        }
        return ToolCallPlan(
            tool_name="finance_releves_sum",
            payload=payload,
            user_reply="OK.",
            meta={
                "followup_from_memory": True,
                "followup_focus": merchant_focus,
                "followup_reason": "merchant_followup",
                "source": "followup",
            },
        )

    if memory.last_tool_name == "finance_releves_sum":
        if not normalized_focus:
            return None
        if not known_categories:
            return None
        if category is None:
            return None
        payload: dict[str, object] = {"direction": "DEBIT_ONLY", **period_payload}
        payload["categorie"] = category
        return ToolCallPlan(
            tool_name="finance_releves_sum",
            payload=payload,
            user_reply="OK.",
            meta={"followup_from_memory": True, "followup_focus": focus, "source": "followup"},
        )

    if memory.last_tool_name == "finance_releves_search":
        if not normalized_focus:
            return None
        payload = {
            "merchant": normalized_focus,
            "limit": 50,
            "offset": 0,
            **period_payload,
        }
        return ToolCallPlan(
            tool_name="finance_releves_search",
            payload=payload,
            user_reply="OK.",
            meta={"followup_from_memory": True, "followup_focus": focus, "source": "followup"},
        )

    if category is not None:
        payload = {"direction": "DEBIT_ONLY", "categorie": category, **period_payload}
        return ToolCallPlan(
            tool_name="finance_releves_sum",
            payload=payload,
            user_reply="OK.",
            meta={
                "followup_from_memory": True,
                "followup_focus": focus,
                "followup_reason": "known_category",
                "source": "followup",
            },
        )

    return None


def _period_payload_from_memory(memory: QueryMemory) -> dict[str, object]:
    if memory.date_range is not None:
        return {"date_range": dict(memory.date_range)}
    if memory.month is not None:
        return {"month": memory.month}
    if memory.year is not None:
        return {"year": memory.year}
    return {}


def _build_period_change_followup_plan(
    message: str,
    memory: QueryMemory,
) -> ToolCallPlan | ClarificationPlan | None:
    period_context = _period_context_from_message(message)
    if period_context is None:
        return None

    month = period_context["month"]
    explicit_year = period_context.get("year")
    year: int | None = explicit_year if isinstance(explicit_year, int) else None

    if year is None:
        inferred_year = _year_from_memory_date_range(memory)
        if inferred_year is None:
            return ClarificationPlan(
                question="Tu veux ce mois de quelle année ?",
                meta={
                    "source": "followup",
                    "clarification_type": "missing_year_for_period",
                    "period_detected": {
                        "month": month,
                        "year": None,
                        "date_range": None,
                    },
                },
            )
        year = inferred_year

    start_date = date(year, month, 1)
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    period_payload = {
        "date_range": {
            "start_date": start_date.isoformat(),
            "end_date": (next_month_start - timedelta(days=1)).isoformat(),
        }
    }

    payload = {
        key: value
        for key, value in memory.filters.items()
        if key not in _PERIOD_KEYS
    }
    payload.update(period_payload)

    return ToolCallPlan(
        tool_name=memory.last_tool_name,
        payload=payload,
        user_reply="OK.",
        meta={
            "followup_from_memory": True,
            "followup_reason": "period_change_followup",
            "source": "followup",
            "period_detected": {
                "month": month,
                "year": year,
                "date_range": period_payload["date_range"],
            },
        },
    )


def _year_from_memory_date_range(memory: QueryMemory) -> int | None:
    if not isinstance(memory.date_range, dict):
        return None
    start_date = memory.date_range.get("start_date")
    if not isinstance(start_date, str):
        return None
    match = re.match(r"^(?P<year>19\d{2}|20\d{2}|21\d{2})-\d{2}-\d{2}$", start_date)
    if match is None:
        return None
    return int(match.group("year"))


def _period_context_from_message(message: str) -> dict[str, object] | None:
    lowered = message.lower()
    month_pattern = "|".join(
        sorted((re.escape(name) for name in _MONTH_LOOKUP), key=len, reverse=True)
    )
    match = re.search(
        rf"\b(?:et\s+)?(?:en|sur)\s+(?P<month>{month_pattern})(?:\s+(?P<year>19\d{{2}}|20\d{{2}}|21\d{{2}}))?\b",
        lowered,
    )
    if match is None:
        return None

    month_name = match.group("month")
    month = _MONTH_LOOKUP.get(month_name)
    if month is None:
        return None

    year_raw = match.group("year")
    return {
        "month": month,
        "year": int(year_raw) if isinstance(year_raw, str) else None,
    }


def _extract_followup_focus(message: str) -> str | None:
    collapsed = re.sub(r"\s+", " ", message.strip())
    if not collapsed:
        return None
    normalized_message = _normalize_text(collapsed)
    if any(token in normalized_message.split() for token in _FOLLOWUP_STOP_TOKENS):
        return None

    matched = re.match(
        r"^(?:ok[\s,.!]+)?(?:et\s+)?(?:en|dans)\s+(.+?)\??$",
        collapsed,
        flags=re.IGNORECASE,
    )
    if matched is None:
        matched = re.match(r"^(?:ok[\s,.!]+)?et\s+(.+?)\??$", collapsed, flags=re.IGNORECASE)
    if matched is not None:
        focus = matched.group(1)
    else:
        raw_tokens = [token for token in re.split(r"\s+", collapsed) if token]
        if len(raw_tokens) > 2:
            return None
        focus = collapsed

    focus = focus.strip(" .,!?:;\"'“”«»")
    focus = re.sub(r"^(?:en|dans|de)\s+", "", focus, flags=re.IGNORECASE)
    normalized_focus = _normalize_text(focus)
    if not normalized_focus or normalized_focus in _NON_FOCUS_MESSAGES:
        return None
    if normalized_focus in _MONTH_LOOKUP or re.fullmatch(r"(?:19\d{2}|20\d{2}|21\d{2})", normalized_focus):
        return None

    return focus or None


def _extract_merchant_focus(message: str) -> str | None:
    collapsed = re.sub(r"\s+", " ", message.strip())
    if not collapsed:
        return None
    match = re.search(r"\bchez\s+(.+?)\??$", collapsed, flags=re.IGNORECASE)
    if match is None:
        return None
    focus = match.group(1).strip(" .,!?:;\"'“”«»")
    if not focus:
        return None
    return focus


def _period_payload_from_message(message: str) -> dict[str, object]:
    lowered = message.lower()
    for month_name, month_number in _MONTH_LOOKUP.items():
        month_match = re.search(rf"\b{re.escape(month_name)}\b", lowered)
        if month_match is None:
            continue
        year_match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", lowered)
        if year_match is None:
            continue
        year = int(year_match.group(1))
        start_date = date(year, month_number, 1)
        if month_number == 12:
            next_month_start = date(year + 1, 1, 1)
        else:
            next_month_start = date(year, month_number + 1, 1)
        return {
            "date_range": {
                "start_date": start_date.isoformat(),
                "end_date": (next_month_start - timedelta(days=1)).isoformat(),
            }
        }

    year_match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", lowered)
    if year_match is not None:
        return {"year": int(year_match.group(1))}
    return {}


def period_payload_from_message(message: str) -> dict[str, object]:
    """Expose explicit period extraction for non-tool clarification turns."""

    return _period_payload_from_message(message)


def _normalize_text(value: str) -> str:
    lowered = value.strip().casefold()
    without_accents = unicodedata.normalize("NFKD", lowered)
    normalized = "".join(
        char for char in without_accents if not unicodedata.combining(char)
    )
    return " ".join(normalized.split())


def _match_known_category(value: str, known_categories: list[str]) -> str | None:
    normalized_target = _normalize_text(value)
    if not normalized_target:
        return None
    for category_name in known_categories:
        if not isinstance(category_name, str):
            continue
        cleaned = category_name.strip()
        if cleaned and _normalize_text(cleaned) == normalized_target:
            return cleaned
    return None



def _known_category_in_message(message: str, known_categories: list[str]) -> str | None:
    normalized_message = _normalize_text(message)
    if not normalized_message:
        return None
    for category_name in known_categories:
        if not isinstance(category_name, str):
            continue
        cleaned = category_name.strip()
        normalized_category = _normalize_text(cleaned)
        if cleaned and normalized_category and normalized_category in normalized_message:
            return cleaned
    return None

def _normalize_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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
        target_key = "categorie" if key in {"category", "categorie"} else key
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
        if target_key not in payload:
            payload[target_key] = value
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


def _looks_like_period_phrase(value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    if "et en" in normalized:
        return True
    return any(month_name in normalized for month_name in _MONTH_LOOKUP)
