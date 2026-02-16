"""Build user-facing final replies from executed tool results."""

from __future__ import annotations

from decimal import Decimal
from difflib import get_close_matches
from typing import Any

from backend.repositories.category_utils import normalize_category_name

from agent.planner import ToolCallPlan
from shared.models import (
    CategoriesListResult,
    ProfileCategory,
    RelevesAggregateResult,
    RelevesDirection,
    RelevesSearchResult,
    RelevesSumResult,
    ToolError,
    ToolErrorCode,
)


def _format_decimal(value: Decimal) -> str:
    """Format decimals without scientific notation."""

    return format(value, "f")


def _debit_only_note(direction: RelevesDirection | None) -> str:
    if direction == RelevesDirection.DEBIT_ONLY:
        return "\nCertaines catégories peuvent être exclues des totaux (ex: Transfert interne)."
    return ""


def _excluded_totals_help_message() -> str:
    return (
        "Une catégorie exclue des totaux (ex: Transfert interne) "
        "n’est pas comptée dans les dépenses."
    )


def _build_category_not_found_reply(plan: ToolCallPlan, error: ToolError) -> str | None:
    if plan.tool_name != "finance_categories_update" or error.code != ToolErrorCode.NOT_FOUND:
        return None
    if not isinstance(plan.payload, dict) or not plan.payload.get("exclude_from_totals"):
        return None

    details: dict[str, Any] = error.details if isinstance(error.details, dict) else {}
    raw_name = details.get("category_name") or plan.payload.get("category_name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    requested_name = raw_name.strip()
    requested_name_norm = normalize_category_name(requested_name)

    candidates = details.get("close_category_names")
    if isinstance(candidates, list):
        candidate_names = [name for name in candidates if isinstance(name, str) and name.strip()]
    else:
        candidate_names = []

    if candidate_names:
        ranked_matches = get_close_matches(
            requested_name_norm,
            [normalize_category_name(name) for name in candidate_names],
            n=3,
            cutoff=0.6,
        )
        if ranked_matches:
            ranked_set = set(ranked_matches)
            display_matches = [
                name
                for name in candidate_names
                if normalize_category_name(name) in ranked_set
            ]
        else:
            display_matches = candidate_names[:3]
        return (
            f"Je ne trouve pas la catégorie « {requested_name} ». "
            f"Voulez-vous dire: {', '.join(display_matches)} ?\n"
            f"{_excluded_totals_help_message()}"
        )

    return (
        f"Je ne trouve pas la catégorie « {requested_name} ». "
        "Souhaitez-vous que je la crée puis l’exclue des totaux ?\n"
        f"{_excluded_totals_help_message()}"
    )


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


def _format_category(item: ProfileCategory) -> str:
    if item.exclude_from_totals:
        return f"- {item.name} (exclue des totaux)"
    return f"- {item.name}"


def _build_categories_list_reply(result: CategoriesListResult) -> str:
    if not result.items:
        return "Vous n'avez aucune catégorie pour le moment."
    lines = [_format_category(item) for item in result.items]
    return "\n".join(["Voici vos catégories :", *lines, _excluded_totals_help_message()])


def build_final_reply(*, plan: ToolCallPlan, tool_result: object) -> str:
    """Build a concise French final answer from a tool result."""

    if isinstance(tool_result, ToolError):
        category_not_found_reply = _build_category_not_found_reply(plan, tool_result)
        if category_not_found_reply is not None:
            return category_not_found_reply
        details = ""
        if tool_result.details:
            details = f" Détails: {tool_result.details}."
        return f"Erreur: {tool_result.message}.{details}".strip()

    if isinstance(tool_result, CategoriesListResult):
        return _build_categories_list_reply(tool_result)

    if isinstance(tool_result, ProfileCategory):
        if plan.tool_name == "finance_categories_create":
            return f"Catégorie créée: {tool_result.name}."
        if plan.tool_name == "finance_categories_update":
            return f"Catégorie mise à jour: {tool_result.name}."

    if plan.tool_name == "finance_categories_delete" and isinstance(tool_result, dict) and tool_result.get("ok"):
        return "Catégorie supprimée."

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
