"""Build user-facing final replies from executed tool results."""

from __future__ import annotations

from decimal import Decimal

from agent.planner import ToolCallPlan
from shared.models import (
    RelevesAggregateResult,
    RelevesDirection,
    RelevesSearchResult,
    RelevesSumResult,
    ToolError,
)


def _format_decimal(value: Decimal) -> str:
    """Format decimals without scientific notation."""

    return format(value, "f")


def _debit_only_note(direction: RelevesDirection | None) -> str:
    if direction == RelevesDirection.DEBIT_ONLY:
        return "\nCertaines catégories peuvent être exclues des totaux (ex: Transfert interne)."
    return ""


def _releves_total_label(result: RelevesSumResult) -> str:
    direction = result.filters.direction if result.filters is not None else None
    if direction == RelevesDirection.DEBIT_ONLY:
        return "Total des dépenses"
    if direction == RelevesDirection.CREDIT_ONLY:
        return "Total des revenus"
    if direction == RelevesDirection.ALL:
        # Convention releves_bancaires: revenus > 0, dépenses < 0, donc ce total est un net.
        return "Total net (revenus + dépenses)"
    return "Total"



def _build_aggregate_reply(result: RelevesAggregateResult) -> str:
    currency = result.currency or "CHF"
    direction = result.filters.direction if result.filters is not None else None
    sorted_groups = sorted(result.groups.items(), key=lambda item: abs(item[1].total), reverse=True)

    if not sorted_groups:
        return "Je n'ai trouvé aucune opération pour cette agrégation."

    top_groups = sorted_groups[:10]
    lines = [
        f"- {name}: {abs(group.total):.2f} {currency} ({group.count} opérations)"
        for name, group in top_groups
    ]

    if len(sorted_groups) > 10:
        others = sorted_groups[10:]
        others_total = sum((abs(group.total) for _, group in others), start=Decimal("0"))
        others_count = sum(group.count for _, group in others)
        lines.append(f"- Autres: {others_total:.2f} {currency} ({others_count} opérations)")

    group_by_label = result.group_by.value
    note = _debit_only_note(direction)
    return "\n".join([f"Voici vos dépenses agrégées par {group_by_label} :", *lines]) + note


def build_final_reply(*, plan: ToolCallPlan, tool_result: object) -> str:
    """Build a concise French final answer from a tool result."""

    if isinstance(tool_result, ToolError):
        details = ""
        if tool_result.details:
            details = f" Détails: {tool_result.details}."
        return f"Erreur: {tool_result.message}.{details}".strip()

    if isinstance(tool_result, RelevesSumResult):
        currency_suffix = f" {tool_result.currency}" if tool_result.currency else ""
        direction = tool_result.filters.direction if tool_result.filters is not None else None
        display_total = abs(tool_result.total) if direction == RelevesDirection.DEBIT_ONLY else tool_result.total
        average = ""
        if tool_result.count > 0:
            average = f" Moyenne: {_format_decimal(tool_result.average)}{currency_suffix}."
        return (
            f"{_releves_total_label(tool_result)}: {_format_decimal(display_total)}{currency_suffix} "
            f"sur {tool_result.count} opération(s).{average}{_debit_only_note(direction)}"
        ).strip()

    if isinstance(tool_result, RelevesAggregateResult):
        return _build_aggregate_reply(tool_result)

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
