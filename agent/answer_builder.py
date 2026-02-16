"""Build user-facing final replies from executed tool results."""

from __future__ import annotations

from decimal import Decimal

from agent.planner import ToolCallPlan
from shared.models import (
    RelevesSearchResult,
    RelevesSumResult,
    ToolError,
    TransactionSearchResult,
    TransactionSumResult,
)


def _format_decimal(value: Decimal) -> str:
    """Format decimals without scientific notation."""

    return format(value, "f")


def build_final_reply(*, plan: ToolCallPlan, tool_result: object) -> str:
    """Build a concise French final answer from a tool result."""

    if isinstance(tool_result, ToolError):
        details = ""
        if tool_result.details:
            details = f" Détails: {tool_result.details}."
        return f"Erreur: {tool_result.message}.{details}".strip()

    if isinstance(tool_result, TransactionSumResult):
        amount = _format_decimal(tool_result.total.amount)
        currency = tool_result.total.currency
        return f"Total: {amount} {currency} sur {tool_result.count} transaction(s)."

    if isinstance(tool_result, RelevesSumResult):
        currency = f" {tool_result.currency}" if tool_result.currency else ""
        average = ""
        if tool_result.count > 0:
            average = f" Moyenne: {_format_decimal(tool_result.average)}{currency}."
        return (
            f"Total des dépenses: {_format_decimal(tool_result.total)}{currency} "
            f"sur {tool_result.count} opération(s).{average}"
        ).strip()

    if isinstance(tool_result, TransactionSearchResult):
        count = len(tool_result.items)
        examples = [item.description for item in tool_result.items[:2] if item.description]
        if examples:
            return f"J'ai trouvé {count} transaction(s), par exemple: {', '.join(examples)}."
        return f"J'ai trouvé {count} transaction(s)."

    if isinstance(tool_result, RelevesSearchResult):
        count = len(tool_result.items)
        examples: list[str] = []
        for item in tool_result.items[:2]:
            label = item.libelle or item.payee
            if label:
                examples.append(label)
        if examples:
            return f"J'ai trouvé {count} opération(s), par exemple: {', '.join(examples)}."
        return f"J'ai trouvé {count} opération(s)."

    return f"{plan.user_reply} (résultat indisponible)"
