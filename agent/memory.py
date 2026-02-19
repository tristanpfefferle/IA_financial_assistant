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
_STICKY_FILTER_KEYS = {"bank_account_id"}
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
_FULL_INTENT_PREFIX_PATTERN = re.compile(
    r"^(?:depenses|revenus?|total)\b"
)
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
_EXPLICIT_FILTER_KEYWORDS = {
    "chez",
    "categorie",
    "categories",
    "catégorie",
    "catégories",
    "merchant",
    "marchand",
}
_DATE_LITERAL_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_CATEGORY_STOPWORDS = {
    "salut",
    "bonjour",
    "merci",
    "ok",
    "oui",
    "non",
    "depenses",
    "dépenses",
    "revenus",
    "revenu",
    "credit",
    "debit",
    "les deux",
}
_NON_FOCUS_MESSAGES = {
    "ok",
    "oui",
    "non",
    "merci",
    "daccord",
    "d'accord",
}

_SEARCH_BEFORE_CHEZ_STOPWORDS = {
    "le",
    "la",
    "les",
    "un",
    "une",
    "du",
    "des",
    "de",
    "d",
}
_SEARCH_BEFORE_CHEZ_PREFIXES = {"et", "ok", "pareil", "idem"}
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
    *,
    known_categories: list[str] | None = None,
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
    _sanitize_memory_filters(filters, known_categories=known_categories or [])

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

    normalized_message = _normalize_text(message)
    if _FULL_INTENT_PREFIX_PATTERN.match(normalized_message):
        return None

    tokens = normalized_message.replace("?", " ").split()
    if len(tokens) >= 3 and not is_followup_message(message):
        return None

    if memory is None or not isinstance(memory.last_tool_name, str):
        return None
    if memory.last_tool_name not in _RELEVES_TOOLS:
        return None

    explicit_period_payload = _period_payload_from_message(message)
    month_only = _month_only_from_message(message)

    merchant_focus = _extract_merchant_focus(message)
    focus = _extract_followup_focus(message)
    category_focus = _known_category_in_message(message, known_categories or [])
    if focus is None and category_focus is None and merchant_focus is None:
        if not explicit_period_payload and month_only is None:
            return None
    if focus is None and category_focus is not None:
        focus = category_focus

    if month_only is not None and not explicit_period_payload:
        last_year: int | None = None
        last_month: int | None = None

        if memory.date_range is not None:
            start_date_raw = memory.date_range.get("start_date")
            if isinstance(start_date_raw, str):
                try:
                    last_start_date = date.fromisoformat(start_date_raw)
                except ValueError:
                    last_start_date = None
                if last_start_date is not None:
                    last_year = last_start_date.year
                    last_month = last_start_date.month

        if last_month is None and isinstance(memory.month, str):
            memory_month = memory.month.strip()
            extracted_month: int | None = None
            year_month_match = re.fullmatch(r"\d{4}-(\d{2})", memory_month)
            if year_month_match is not None:
                extracted_month = int(year_month_match.group(1))
            elif re.fullmatch(r"\d{1,2}", memory_month):
                extracted_month = int(memory_month)
            if extracted_month is not None and 1 <= extracted_month <= 12:
                last_month = extracted_month

        if last_year is None and memory.year is not None:
            last_year = memory.year

        if last_year is None:
            return None

        requested_year = (
            last_year + 1
            if last_month == 12 and month_only == 1
            else last_year
        )
        explicit_period_payload = {
            "date_range": _month_date_range_payload(requested_year, month_only)
        }

    if explicit_period_payload and _is_memory_period_followup_candidate(message):
        payload = {
            **_period_payload_from_memory(memory),
            **dict(memory.filters),
            **explicit_period_payload,
        }
        if category_focus is not None:
            payload["categorie"] = category_focus
        if "date_range" in explicit_period_payload:
            payload.pop("month", None)
            payload.pop("year", None)
        payload.pop("limit", None)
        payload.pop("offset", None)
        return ToolCallPlan(
            tool_name=memory.last_tool_name,
            payload=payload,
            user_reply="OK.",
            meta={
                "followup_from_memory": True,
                "followup_reason": "explicit_period_reuses_last_query",
            },
        )

    period_payload = explicit_period_payload or _period_payload_from_memory(memory)
    normalized_focus = _normalize_text(focus) if isinstance(focus, str) else ""
    category = (
        _match_known_category(focus, known_categories or []) if isinstance(focus, str) else None
    )

    if category_focus is not None and memory.last_tool_name == "finance_releves_search":
        direction = memory.filters.get("direction")
        normalized_direction = (
            direction.strip().upper()
            if isinstance(direction, str) and direction.strip()
            else "DEBIT_ONLY"
        )
        payload = {
            "direction": normalized_direction,
            "categorie": category_focus,
            **period_payload,
        }
        return ToolCallPlan(
            tool_name="finance_releves_sum",
            payload=payload,
            user_reply="OK.",
            meta={
                "followup_from_memory": True,
                "followup_focus": category_focus,
                "followup_reason": "known_category_after_search",
            },
        )

    if merchant_focus is not None:
        normalized_merchant = _normalize_text(merchant_focus)
        if not normalized_merchant:
            return None

        search_term = _extract_search_term_before_chez(message)
        if search_term is not None:
            return ClarificationPlan(
                question=(
                    f"Tu veux chercher le marchand ‘{merchant_focus}’ "
                    f"ou le mot-clé ‘{search_term}’ ?"
                ),
                meta={
                    "keep_active_task": True,
                    "clarification_type": "merchant_vs_keyword",
                    "clarification_payload": {
                        "merchant": merchant_focus,
                        "keyword": search_term,
                    },
                },
            )

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
            meta={"followup_from_memory": True, "followup_focus": focus},
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
            meta={"followup_from_memory": True, "followup_focus": focus},
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


def _extract_search_term_before_chez(message: str) -> str | None:
    collapsed = re.sub(r"\s+", " ", message.strip())
    if not collapsed:
        return None

    match = re.search(r"^(.+?)\bchez\s+.+?$", collapsed, flags=re.IGNORECASE)
    if match is None:
        return None

    before_chez = _normalize_text(match.group(1).strip(" .,!?:;\"'“”«»"))
    if not before_chez:
        return None

    tokens = before_chez.split()
    while tokens and tokens[0] in _SEARCH_BEFORE_CHEZ_PREFIXES:
        tokens.pop(0)
    while tokens and tokens[0] in _SEARCH_BEFORE_CHEZ_STOPWORDS:
        tokens.pop(0)

    if not tokens:
        return None

    candidate_tokens = [token for token in tokens if token not in _SEARCH_BEFORE_CHEZ_STOPWORDS]
    if not candidate_tokens:
        return None

    return " ".join(candidate_tokens[:3])


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
        return {
            "date_range": _month_date_range_payload(year, month_number)
        }

    year_match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", lowered)
    if year_match is not None:
        return {"year": int(year_match.group(1))}
    return {}


def _month_only_from_message(message: str) -> int | None:
    lowered = message.lower()
    has_year = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", lowered) is not None
    if has_year:
        return None
    for month_name, month_number in _MONTH_LOOKUP.items():
        if re.search(rf"\b{re.escape(month_name)}\b", lowered):
            return month_number
    return None


def _month_date_range_payload(year: int, month: int) -> dict[str, str]:
    start_date = date(year, month, 1)
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    return {
        "start_date": start_date.isoformat(),
        "end_date": (next_month_start - timedelta(days=1)).isoformat(),
    }


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
    had_explicit_merchant = "merchant" in payload
    sum_has_category = (
        plan.tool_name == "finance_releves_sum"
        and isinstance(payload.get("categorie"), str)
        and bool(str(payload.get("categorie", "")).strip())
    )
    period_injected = False
    filter_injected = False
    nested_filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else None
    has_period = any(key in payload for key in _PERIOD_KEYS) or (
        isinstance(nested_filters, dict) and "date_range" in nested_filters
    )
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

    sticky_filter_injected = _inject_sticky_filters(payload, memory.filters)
    if sticky_filter_injected:
        reason_parts.append("sticky_filters_from_memory")

    if is_followup_message(message):
        filter_injected = _merge_missing_filters(
            payload,
            memory.filters,
            block_merchant_injection=sum_has_category,
        )
        if filter_injected:
            reason_parts.append("followup_filters_from_memory")

    if sum_has_category and not had_explicit_merchant:
        payload.pop("merchant", None)

    if not period_injected and not filter_injected and not sticky_filter_injected:
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
    *,
    block_merchant_injection: bool = False,
) -> bool:
    injected = False
    for key, value in memory_filters.items():
        if key in _PERIOD_KEYS:
            continue
        target_key = "categorie" if key in {"category", "categorie"} else key
        if target_key in {"merchant", "search"} and block_merchant_injection:
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
        if target_key not in payload:
            payload[target_key] = value
            injected = True
    return injected


def _inject_sticky_filters(payload: dict[str, Any], memory_filters: dict[str, Any]) -> bool:
    injected = False
    for key in _STICKY_FILTER_KEYS:
        value = memory_filters.get(key)
        if value is None:
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


def _is_memory_period_followup_candidate(message: str) -> bool:
    normalized = _normalize_text(message)
    if not normalized:
        return False

    tokens = set(normalized.replace("?", " ").split())
    has_explicit_intent = any(token in _INTENT_KEYWORDS for token in tokens)
    has_explicit_filter = any(token in _EXPLICIT_FILTER_KEYWORDS for token in tokens)

    return not has_explicit_intent and not has_explicit_filter


def _sanitize_memory_filters(
    filters: dict[str, Any],
    *,
    known_categories: list[str],
) -> None:
    raw_category = filters.get("categorie")
    if not isinstance(raw_category, str) or not raw_category.strip():
        return

    normalized_category = _normalize_text(raw_category)
    if not normalized_category:
        filters.pop("categorie", None)
        return

    if known_categories:
        matched = _match_known_category(raw_category, known_categories)
        if matched is None:
            filters.pop("categorie", None)
            return
        filters["categorie"] = matched
        return

    if normalized_category in _CATEGORY_STOPWORDS:
        filters.pop("categorie", None)
        return

    if _DATE_LITERAL_PATTERN.search(raw_category) is not None:
        filters.pop("categorie", None)
