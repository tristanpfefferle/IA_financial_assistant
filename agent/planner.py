"""Deterministic planning for user messages (LLM-free for now)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
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


@dataclass(slots=True)
class ClarificationPlan:
    """Plan that asks the user for clarification."""

    question: str


@dataclass(slots=True)
class NoopPlan:
    """Plan that returns a response without calling tools."""

    reply: str


@dataclass(slots=True)
class ErrorPlan:
    """Plan that returns a parsing error without invoking tools."""

    reply: str
    tool_error: ToolError


Plan = ToolCallPlan | ClarificationPlan | NoopPlan | ErrorPlan


_SEARCH_TOKENS = {"from", "to", "account", "category", "limit", "offset", "min", "max"}


def _parse_search_command(message: str) -> tuple[dict[str, object] | None, ToolError | None]:
    """Parse `search:` commands.

    Grammar: `search: <term?> [from:YYYY-MM-DD to:YYYY-MM-DD] [account:<id>]`
    with optional `category:`, `limit:`, `offset:`, `min:` and `max:` filters.
    """
    body = message.split(":", maxsplit=1)[1].strip()
    if not body:
        return {"search": None, "limit": 50, "offset": 0}, None

    words = body.split()
    search_parts: list[str] = []
    first_token_index = len(words)

    for index, word in enumerate(words):
        token, _, _ = word.partition(":")
        if token.lower() in _SEARCH_TOKENS and ":" in word:
            first_token_index = index
            break
        search_parts.append(word)

    payload: dict[str, object] = {
        "search": " ".join(search_parts).strip() or None,
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

        if "account" in token_values:
            payload["account_id"] = token_values["account"]

        if "category" in token_values:
            payload["category_id"] = token_values["category"]

        if "limit" in token_values:
            payload["limit"] = int(token_values["limit"])

        if "offset" in token_values:
            payload["offset"] = int(token_values["offset"])

        if "min" in token_values:
            payload["min_amount"] = Decimal(token_values["min"])

        if "max" in token_values:
            payload["max_amount"] = Decimal(token_values["max"])
    except ValueError as exc:
        return None, ToolError(
            code=ToolErrorCode.VALIDATION_ERROR,
            message="Format invalide dans la commande search:. Vérifiez les dates et nombres.",
            details={"error": str(exc), "input": token_values},
        )
    except InvalidOperation as exc:
        return None, ToolError(
            code=ToolErrorCode.VALIDATION_ERROR,
            message="Montant invalide dans la commande search:. Utilisez un nombre décimal valide.",
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
            tool_name="finance.transactions.search",
            payload=payload or {},
            user_reply="Voici le résultat de la recherche de transactions.",
        )

    return NoopPlan(reply="Commandes disponibles: 'ping' ou 'search: <term>'.")


def plan_from_message(message: str, llm_planner: LLMPlanner | None = None) -> Plan:
    """Build a plan from a user message, optionally delegating to an LLM planner."""

    plan = deterministic_plan_from_message(message)

    if isinstance(plan, (ToolCallPlan, ErrorPlan)):
        return plan

    if isinstance(plan, NoopPlan) and plan.reply == "pong":
        return plan

    if llm_planner is not None:
        return llm_planner.plan(message)

    return NoopPlan(reply="Commandes disponibles: 'ping' ou 'search: <term>'.")
