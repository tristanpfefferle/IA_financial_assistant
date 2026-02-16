"""Deterministic planning for user messages (LLM-free for now)."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from shared.models import ToolError, ToolErrorCode

if TYPE_CHECKING:
    from agent.llm_planner import LLMPlanner


@dataclass(slots=True)
class ToolCallPlan:
    """Plan that invokes a backend tool."""

    tool_name: str
    payload: dict[str, object]
    user_reply: str
    meta: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ClarificationPlan:
    """Plan that asks the user for clarification."""

    question: str
    meta: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class NoopPlan:
    """Plan that returns a response without calling tools."""

    reply: str
    meta: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ErrorPlan:
    """Plan that returns a parsing error without invoking tools."""

    reply: str
    tool_error: ToolError


@dataclass(slots=True)
class SetActiveTaskPlan:
    """Plan that stores a pending action before user confirmation."""

    reply: str
    active_task: dict[str, object]


Plan = ToolCallPlan | ClarificationPlan | NoopPlan | ErrorPlan | SetActiveTaskPlan


_SEARCH_TOKENS = {"from", "to", "category", "limit", "offset"}
_FRENCH_MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
}
_FRENCH_MONTH_ALIASES = {
    "janv": 1,
    "janv.": 1,
    "fevr": 2,
    "fevr.": 2,
    "févr": 2,
    "févr.": 2,
    "avr": 4,
    "avr.": 4,
    "juil": 7,
    "juil.": 7,
    "sept": 9,
    "sept.": 9,
    "oct": 10,
    "oct.": 10,
    "nov": 11,
    "nov.": 11,
    "dec": 12,
    "dec.": 12,
    "déc": 12,
    "déc.": 12,
}
_EXPENSE_KEYWORDS = {"depense", "dépense", "depenses", "dépenses"}

_AGGREGATE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("par catégorie", "par categorie"), "categorie"),
    (("par marchand", "par commerçant", "par commercant"), "payee"),
    (("par mois",), "month"),
)


_CATEGORY_LIST_PATTERNS = (
    "liste mes catégories",
    "liste mes categories",
    "quelles sont mes catégories",
    "quelles sont mes categories",
)

_CATEGORY_DELETE_PATTERNS = (
    "supprime la catégorie",
    "supprimer la catégorie",
    "delete la catégorie",
    "remove la catégorie",
    "efface la catégorie",
)

_CATEGORY_RENAME_PATTERNS = (
    "renomme la catégorie",
    "renommer la catégorie",
    "change le nom de la catégorie",
    "modifie le nom de la catégorie",
    "modifie la catégorie",
    "modifier la catégorie",
    "change la catégorie",
    "changer la catégorie",
    "appelle la catégorie",
)

_QUOTED_VALUE_PATTERN = r"[\"'«](?P<value>[^\"'»]+)[\"'»]"


def _aggregate_group_by_for_message(lower_message: str) -> str | None:
    for keywords, group_by in _AGGREGATE_RULES:
        if any(keyword in lower_message for keyword in keywords):
            return group_by
    return None


def _today() -> date:
    return date.today()


def _extract_month(lower_message: str) -> int | None:
    tokenized_message = re.findall(r"[\wéèêëàâäùûüôöîïç\.]+", lower_message)
    month_lookup = {**_FRENCH_MONTHS, **_FRENCH_MONTH_ALIASES}
    return next((month_lookup[token] for token in tokenized_message if token in month_lookup), None)


def _extract_year(message: str) -> int | None:
    years = re.findall(r"\b(19\d{2}|20\d{2}|21\d{2})\b", message)
    if not years:
        return None
    return int(years[0])


def _extract_category_name(message: str, pattern: str) -> str | None:
    match = re.search(pattern, message, flags=re.IGNORECASE)
    if match is None:
        return None
    category_name = match.group("category").strip(" .,!?:;\"'")
    return category_name or None




def _extract_quoted_values(message: str) -> list[str]:
    return [match.group("value").strip() for match in re.finditer(_QUOTED_VALUE_PATTERN, message)]


def _extract_category_name_after_keyword(message: str) -> str | None:
    match = re.search(r"catégorie\s+(?P<category>.+)$", message, flags=re.IGNORECASE)
    if match is None:
        return None
    category_name = match.group("category").strip(" .,!?:;\"'")
    return category_name or None


def _extract_rename_names(message: str) -> tuple[str, str] | None:
    quoted_values = _extract_quoted_values(message)
    if len(quoted_values) >= 2:
        return quoted_values[0], quoted_values[1]

    match = re.search(r"catégorie\s+(?P<old>.+?)\s+en\s+(?P<new>.+)$", message, flags=re.IGNORECASE)
    if match is None:
        return None

    old_name = match.group("old").strip(" .,!?:;\"'")
    new_name = match.group("new").strip(" .,!?:;\"'")
    if not old_name or not new_name:
        return None
    return old_name, new_name


def _extract_delete_name(message: str) -> str | None:
    quoted_values = _extract_quoted_values(message)
    if quoted_values:
        return quoted_values[0]
    return _extract_category_name_after_keyword(message)


def _build_delete_plan(message: str) -> SetActiveTaskPlan | ClarificationPlan:
    category_name = _extract_delete_name(message)
    if category_name is None:
        return ClarificationPlan(question="Quelle catégorie voulez-vous supprimer ?")
    return SetActiveTaskPlan(
        reply=f"Confirmez-vous la suppression de « {category_name} » ? Répondez OUI ou NON.",
        active_task={
            "type": "confirm_delete_category",
            "category_name": category_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _build_rename_plan(message: str) -> ToolCallPlan | ClarificationPlan:
    names = _extract_rename_names(message)
    if names is None:
        return ClarificationPlan(question="Quelle catégorie voulez-vous renommer, et en quel nom ?")
    old_name, new_name = names
    return ToolCallPlan(
        tool_name="finance_categories_update",
        payload={"category_name": old_name, "name": new_name},
        user_reply="Catégorie renommée.",
    )


def _parse_search_command(message: str) -> tuple[dict[str, object] | None, ToolError | None]:
    """Parse `search:` commands.

    Grammar: `search: <merchant?> [from:YYYY-MM-DD to:YYYY-MM-DD]`
    with optional `category:`, `limit:` and `offset:` filters.
    """
    body = message.split(":", maxsplit=1)[1].strip()
    if not body:
        return {"merchant": None, "limit": 50, "offset": 0}, None

    words = body.split()
    search_parts: list[str] = []
    first_token_index = len(words)

    for index, word in enumerate(words):
        token, _, _ = word.partition(":")
        if token.lower() in _SEARCH_TOKENS and ":" in word:
            first_token_index = index
            break
        search_parts.append(word)

    # UX note: the free-text term before filters maps to `merchant`.
    payload: dict[str, object] = {
        "merchant": " ".join(search_parts).strip() or None,
        "limit": 50,
        "offset": 0,
    }
    token_values: dict[str, str] = {}
    for raw in words[first_token_index:]:
        token, sep, value = raw.partition(":")
        if not sep:
            continue
        token_key = token.lower()
        if token_key in _SEARCH_TOKENS:
            token_values[token_key] = value

    try:
        if "from" in token_values or "to" in token_values:
            if "from" not in token_values or "to" not in token_values:
                return None, ToolError(
                    code=ToolErrorCode.VALIDATION_ERROR,
                    message="Les dates doivent inclure from:YYYY-MM-DD et to:YYYY-MM-DD.",
                    details={"from": token_values.get("from"), "to": token_values.get("to")},
                )
            payload["date_range"] = {
                "start_date": date.fromisoformat(token_values["from"]),
                "end_date": date.fromisoformat(token_values["to"]),
            }

        if "category" in token_values:
            payload["categorie"] = token_values["category"]

        if "limit" in token_values:
            payload["limit"] = int(token_values["limit"])

        if "offset" in token_values:
            payload["offset"] = int(token_values["offset"])

    except ValueError as exc:
        return None, ToolError(
            code=ToolErrorCode.VALIDATION_ERROR,
            message="Format invalide dans la commande search:. Vérifiez les dates et nombres.",
            details={"error": str(exc), "input": token_values},
        )
    return payload, None


def deterministic_plan_from_message(message: str) -> Plan:
    """Build a deterministic execution plan from a user message."""

    normalized_message = message.strip()

    if normalized_message.lower() == "ping":
        return NoopPlan(reply="pong")

    if normalized_message.lower().startswith("search:"):
        payload, parse_error = _parse_search_command(normalized_message)
        if parse_error is not None:
            return ErrorPlan(
                reply="Je n'ai pas pu interpréter la commande search:. Corrigez le format puis réessayez.",
                tool_error=parse_error,
            )

        return ToolCallPlan(
            tool_name="finance_releves_search",
            payload=payload or {},
            user_reply="Voici le résultat de la recherche de relevés.",
        )

    lower_message = normalized_message.lower()

    if any(pattern in lower_message for pattern in _CATEGORY_LIST_PATTERNS):
        return ToolCallPlan(
            tool_name="finance_categories_list",
            payload={},
            user_reply="Voici vos catégories.",
        )

    if any(pattern in lower_message for pattern in _CATEGORY_DELETE_PATTERNS):
        return _build_delete_plan(normalized_message)

    if any(pattern in lower_message for pattern in _CATEGORY_RENAME_PATTERNS):
        return _build_rename_plan(normalized_message)

    category_to_exclude = _extract_category_name(
        normalized_message,
        r"exclus\s+(?P<category>.+?)\s+des\s+totaux",
    )
    if category_to_exclude is not None:
        return ToolCallPlan(
            tool_name="finance_categories_update",
            payload={"category_name": category_to_exclude, "exclude_from_totals": True},
            user_reply="Catégorie exclue des totaux.",
        )

    category_to_include = _extract_category_name(
        normalized_message,
        r"(?:réintègre|reintegre|inclue|inclu)\s+(?P<category>.+)",
    )
    if category_to_include is not None:
        return ToolCallPlan(
            tool_name="finance_categories_update",
            payload={"category_name": category_to_include, "exclude_from_totals": False},
            user_reply="Catégorie réintégrée dans les totaux.",
        )

    aggregate_group_by = _aggregate_group_by_for_message(lower_message)
    if aggregate_group_by is not None:
        return ToolCallPlan(
            tool_name="finance_releves_aggregate",
            payload={
                "group_by": aggregate_group_by,
                "direction": "DEBIT_ONLY",
            },
            user_reply="OK, je prépare une vue agrégée de vos dépenses.",
        )

    if any(keyword in lower_message for keyword in _EXPENSE_KEYWORDS):
        month = _extract_month(lower_message)
        if month is not None:
            explicit_year = _extract_year(normalized_message)
            today = _today()
            year = explicit_year or today.year
            if explicit_year is None and month > today.month:
                return ClarificationPlan(question="De quelle année parlez-vous ?")
            last_day = calendar.monthrange(year, month)[1]
            return ToolCallPlan(
                tool_name="finance_releves_sum",
                payload={
                    "direction": "DEBIT_ONLY",
                    "date_range": {
                        "start_date": date(year, month, 1),
                        "end_date": date(year, month, last_day),
                    },
                },
                user_reply="OK, je calcule le total de vos dépenses.",
            )

    return NoopPlan(reply="Commandes disponibles: 'ping' ou 'search: <term>'.")


def plan_from_message(
    message: str,
    llm_planner: LLMPlanner | None = None,
) -> Plan:
    """Build a plan from a user message, optionally delegating to an LLM planner."""

    plan = deterministic_plan_from_message(message)

    # We intentionally return ClarificationPlan directly so the UX can ask the
    # follow-up question deterministically before any optional LLM fallback.
    if isinstance(plan, (ToolCallPlan, ErrorPlan, ClarificationPlan, SetActiveTaskPlan)):
        return plan

    if isinstance(plan, NoopPlan) and plan.reply == "pong":
        return plan

    if llm_planner is not None:
        return llm_planner.plan(message)

    return NoopPlan(reply="Commandes disponibles: 'ping' ou 'search: <term>'.")
