"""Build user-facing final replies from executed tool results."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from agent.planner import ToolCallPlan
from shared.models import (
    BankAccount,
    BankAccountsListResult,
    CategoriesListResult,
    ProfileDataResult,
    ProfileCategory,
    RelevesAggregateResult,
    RelevesDirection,
    RelevesImportResult,
    RelevesSearchResult,
    RelevesSumResult,
    ToolError,
    ToolErrorCode,
)


PROFILE_FIELD_LABELS: dict[str, str] = {
    "first_name": "Prénom",
    "last_name": "Nom",
    "birth_date": "Date de naissance",
    "gender": "Genre",
    "address_line1": "Adresse",
    "address_line2": "Complément d’adresse",
    "postal_code": "Code postal",
    "city": "Ville",
    "canton": "Canton",
    "country": "Pays",
    "personal_situation": "Situation personnelle",
    "professional_situation": "Situation professionnelle",
    "default_bank_account_id": "Compte bancaire par défaut",
    "active_modules": "Modules actifs",
}

PROFILE_FIELD_POSSESSIVE: dict[str, str] = {
    "first_name": "prénom",
    "last_name": "nom",
    "birth_date": "date de naissance",
    "gender": "genre",
    "address_line1": "adresse",
    "address_line2": "complément d’adresse",
    "postal_code": "code postal",
    "city": "ville",
    "canton": "canton",
    "country": "pays",
    "personal_situation": "situation personnelle",
    "professional_situation": "situation professionnelle",
    "default_bank_account_id": "compte bancaire par défaut",
    "active_modules": "modules actifs",
}


def format_money(
    amount: Decimal | int | float | str,
    currency: str = "CHF",
) -> str:
    """Format a monetary amount with two decimals and optional currency."""

    decimal_amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{decimal_amount:.2f} {currency}" if currency else f"{decimal_amount:.2f}"


def _debit_only_note(direction: RelevesDirection | None) -> str:
    if direction == RelevesDirection.DEBIT_ONLY:
        return "\nCertaines catégories peuvent être exclues des totaux (ex: Transfert interne)."
    return ""


def _excluded_totals_help_message() -> str:
    return (
        "Une catégorie exclue des totaux (ex: Transfert interne) "
        "n’est pas comptée dans les dépenses."
    )


def _build_category_not_found_reply(error: ToolError) -> str | None:
    if error.code != ToolErrorCode.NOT_FOUND:
        return None

    details: dict[str, Any] = error.details if isinstance(error.details, dict) else {}
    raw_name = details.get("category_name")
    category_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None

    base_message = (
        f"Je ne trouve pas la catégorie « {category_name} »."
        if category_name is not None
        else "Je ne trouve pas cette catégorie."
    )

    candidate_names_raw = details.get("close_category_names")
    if not isinstance(candidate_names_raw, list):
        return f"{base_message} Souhaitez-vous la créer ?"

    candidate_names = [name for name in candidate_names_raw if isinstance(name, str) and name.strip()]
    if candidate_names:
        return f"{base_message} Vouliez-vous dire: {', '.join(candidate_names[:3])} ?"

    return f"{base_message} Souhaitez-vous la créer ?"


def _build_category_ambiguous_reply(error: ToolError) -> str | None:
    if error.code != ToolErrorCode.AMBIGUOUS:
        return None

    details: dict[str, Any] = error.details if isinstance(error.details, dict) else {}
    candidates_raw = details.get("candidates")
    if not isinstance(candidates_raw, list):
        return "Plusieurs catégories correspondent. Pouvez-vous préciser ?"

    candidates = [name for name in candidates_raw if isinstance(name, str) and name.strip()]
    if not candidates:
        return "Plusieurs catégories correspondent. Pouvez-vous préciser ?"

    return f"Plusieurs catégories correspondent: {', '.join(candidates)}."


def _build_bank_account_not_found_reply(error: ToolError) -> str | None:
    if error.code != ToolErrorCode.NOT_FOUND:
        return None
    details: dict[str, Any] = error.details if isinstance(error.details, dict) else {}
    if "name" not in details:
        return None
    raw_name = details.get("name")
    account_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    base_message = (
        f"Je ne trouve pas le compte « {account_name} »." if account_name else "Je ne trouve pas ce compte."
    )
    close_names = details.get("close_names")
    if isinstance(close_names, list) and close_names:
        return f"{base_message} Vouliez-vous dire: {', '.join(str(name) for name in close_names[:3])} ?"
    return base_message


def _build_bank_account_ambiguous_reply(error: ToolError) -> str | None:
    if error.code != ToolErrorCode.AMBIGUOUS:
        return None
    details: dict[str, Any] = error.details if isinstance(error.details, dict) else {}
    if "candidates" not in details:
        return None
    candidates_raw = details.get("candidates")
    if not isinstance(candidates_raw, list):
        return None
    names: list[str] = []
    for candidate in candidates_raw:
        if isinstance(candidate, dict) and isinstance(candidate.get("name"), str):
            names.append(candidate["name"])
    if not names:
        return None
    return f"Plusieurs comptes correspondent: {', '.join(names)}."


def _build_bank_account_create_conflict_reply(plan: ToolCallPlan, error: ToolError) -> str | None:
    if plan.tool_name != "finance_bank_accounts_create":
        return None
    if error.code != ToolErrorCode.CONFLICT:
        return None
    account_name = plan.payload.get("name") if isinstance(plan.payload, dict) else None
    if isinstance(account_name, str) and account_name.strip():
        return f"Un compte nommé « {account_name} » existe déjà. Choisissez un autre nom."
    return "Un compte portant ce nom existe déjà. Choisissez un autre nom."

def _build_bank_account_delete_conflict_reply(plan: ToolCallPlan, error: ToolError) -> str | None:
    if plan.tool_name != "finance_bank_accounts_delete":
        return None
    if error.code != ToolErrorCode.CONFLICT:
        return None
    return (
        "Impossible de supprimer ce compte car il contient des transactions. "
        "Déplacez/supprimez d’abord les transactions ou choisissez un autre compte."
    )


def _build_profile_validation_reply(plan: ToolCallPlan, error: ToolError) -> str | None:
    if plan.tool_name not in {"finance_profile_get", "finance_profile_update"}:
        return None
    if error.code != ToolErrorCode.VALIDATION_ERROR:
        return None

    details: dict[str, Any] = error.details if isinstance(error.details, dict) else {}
    raw_field = details.get("field")
    if isinstance(raw_field, str) and raw_field.strip():
        return (
            "Je n’ai pas compris quelle info du profil vous voulez "
            "(prénom, nom, ville, etc.)."
        )
    return "La demande de profil est invalide. Précisez un champ comme prénom, nom ou ville."

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


def _format_profile_value(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _profile_field_label(field_name: str) -> str:
    return PROFILE_FIELD_LABELS.get(field_name, field_name)


def _profile_field_possessive(field_name: str) -> str:
    return PROFILE_FIELD_POSSESSIVE.get(field_name, field_name)


def _build_profile_get_reply(plan: ToolCallPlan, result: ProfileDataResult) -> str:
    requested_fields_raw = plan.payload.get("fields") if isinstance(plan.payload, dict) else None
    if isinstance(requested_fields_raw, list):
        requested_fields = [field for field in requested_fields_raw if isinstance(field, str)]
    else:
        requested_fields = list(result.data.keys())

    if not requested_fields:
        requested_fields = list(result.data.keys())

    if len(requested_fields) == 1:
        field_name = requested_fields[0]
        value = result.data.get(field_name)
        if value in (None, ""):
            return f"Je n’ai pas votre {_profile_field_possessive(field_name)} (champ vide)."
        return f"Votre {_profile_field_possessive(field_name)} est: {_format_profile_value(value)}."

    lines = []
    for field_name in requested_fields:
        value = result.data.get(field_name)
        formatted_value = _format_profile_value(value) if value not in (None, "") else "(vide)"
        lines.append(f"- {_profile_field_label(field_name)}: {formatted_value}")
    return "\n".join(lines)


def _build_profile_update_reply(result: ProfileDataResult) -> str:
    lines = ["Infos mises à jour."]
    for field_name, value in result.data.items():
        if value is None:
            lines.append(f"Champ effacé: {_profile_field_possessive(field_name)}.")
            continue
        lines.append(f"- {_profile_field_label(field_name)}: {_format_profile_value(value)}")
    return "\n".join(lines)


def build_final_reply(*, plan: ToolCallPlan, tool_result: object) -> str:
    """Build a concise French final answer from a tool result."""

    if isinstance(tool_result, ToolError):
        bank_account_not_found_reply = _build_bank_account_not_found_reply(tool_result)
        if bank_account_not_found_reply is not None:
            return bank_account_not_found_reply
        bank_account_ambiguous_reply = _build_bank_account_ambiguous_reply(tool_result)
        if bank_account_ambiguous_reply is not None:
            return bank_account_ambiguous_reply
        bank_account_create_conflict_reply = _build_bank_account_create_conflict_reply(plan, tool_result)
        if bank_account_create_conflict_reply is not None:
            return bank_account_create_conflict_reply
        bank_account_delete_conflict_reply = _build_bank_account_delete_conflict_reply(plan, tool_result)
        if bank_account_delete_conflict_reply is not None:
            return bank_account_delete_conflict_reply
        category_not_found_reply = _build_category_not_found_reply(tool_result)
        if category_not_found_reply is not None:
            return category_not_found_reply
        category_ambiguous_reply = _build_category_ambiguous_reply(tool_result)
        if category_ambiguous_reply is not None:
            return category_ambiguous_reply
        profile_validation_reply = _build_profile_validation_reply(plan, tool_result)
        if profile_validation_reply is not None:
            return profile_validation_reply
        details = ""
        if tool_result.details:
            details = f" Détails: {tool_result.details}."
        return f"Erreur: {tool_result.message}.{details}".strip()

    if isinstance(tool_result, CategoriesListResult):
        return _build_categories_list_reply(tool_result)

    if isinstance(tool_result, BankAccountsListResult):
        if not tool_result.items:
            return "Vous n'avez aucun compte bancaire pour le moment."
        default_id = tool_result.default_bank_account_id

        lines = []

        for item in tool_result.items:
            star = ""
            if default_id is not None and item.id == default_id:
                star = " ⭐"

            lines.append(
                f"- {item.name}{star} ({item.account_kind or 'inconnu'}, {item.kind or 'inconnu'})"
            )

        return "\n".join(lines)

    if isinstance(tool_result, ProfileDataResult):
        if plan.tool_name == "finance_profile_get":
            return _build_profile_get_reply(plan, tool_result)
        if plan.tool_name == "finance_profile_update":
            return _build_profile_update_reply(tool_result)

    if isinstance(tool_result, ProfileCategory):
        if plan.tool_name == "finance_categories_create":
            return f"Catégorie créée: {tool_result.name}."
        if plan.tool_name == "finance_categories_update":
            requested_old_name = plan.payload.get("category_name") if isinstance(plan.payload, dict) else None
            requested_new_name = plan.payload.get("name") if isinstance(plan.payload, dict) else None
            if isinstance(requested_old_name, str) and isinstance(requested_new_name, str):
                return f"Catégorie renommée : {requested_old_name} → {requested_new_name}."
            return f"Catégorie mise à jour: {tool_result.name}."

    if isinstance(tool_result, BankAccount):
        if plan.tool_name == "finance_bank_accounts_create":
            return f"Compte créé: {tool_result.name}."
        if plan.tool_name == "finance_bank_accounts_update":
            return f"Compte mis à jour: {tool_result.name}."

    if plan.tool_name == "finance_categories_delete" and isinstance(tool_result, dict) and tool_result.get("ok"):
        deleted_name = plan.payload.get("category_name") if isinstance(plan.payload, dict) else None
        if isinstance(deleted_name, str):
            return f"Catégorie supprimée : {deleted_name}."
        return "Catégorie supprimée."

    if plan.tool_name == "finance_bank_accounts_delete" and isinstance(tool_result, dict) and tool_result.get("ok"):
        deleted_name = plan.payload.get("name") if isinstance(plan.payload, dict) else None
        if isinstance(deleted_name, str):
            return f"Compte supprimé: {deleted_name}."
        return "Compte supprimé."

    if plan.tool_name == "finance_bank_accounts_set_default" and isinstance(tool_result, dict) and tool_result.get("ok"):
        account_name = plan.payload.get("name") if isinstance(plan.payload, dict) else None
        if isinstance(account_name, str):
            return f"Compte par défaut: {account_name}."
        return "Compte par défaut défini."

    if plan.tool_name == "finance_releves_set_bank_account" and isinstance(tool_result, dict) and tool_result.get("ok"):
        updated_count = tool_result.get("updated_count")
        account_name = None
        if isinstance(plan.payload, dict):
            raw_name = plan.payload.get("bank_account_name") or plan.payload.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                account_name = raw_name.strip()
        if isinstance(updated_count, int):
            if account_name is not None:
                return f"OK — j’ai rattaché {updated_count} transaction(s) au compte « {account_name} »."
            return f"OK — j’ai rattaché {updated_count} transaction(s) au compte."
        return "OK — transactions rattachées au compte."

    if isinstance(tool_result, RelevesSumResult):
        currency = tool_result.currency or ""
        direction = tool_result.filters.direction if tool_result.filters is not None else None
        average = ""
        if tool_result.count > 0:
            average = f" Moyenne: {format_money(tool_result.average, currency)}."
        return (
            f"{_releves_total_label(tool_result)}: {format_money(tool_result.total, currency)} "
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

    if isinstance(tool_result, RelevesImportResult):
        if plan.payload.get("import_mode", "analyze") == "commit":
            message = (
                f"Import terminé : {tool_result.imported_count} importée(s), "
                f"{tool_result.replaced_count} remplacée(s), {tool_result.identical_count} identique(s)."
            )
        elif tool_result.requires_confirmation:
            message = (
                f"Analyse terminée : {tool_result.new_count} nouvelles, "
                f"{tool_result.identical_count} identiques, {tool_result.modified_count} modifiées. "
                "Confirmer pour importer (commit) / choisir replace si besoin."
            )
        else:
            message = (
                f"Analyse terminée : {tool_result.new_count} nouvelles, "
                f"{tool_result.identical_count} identiques, {tool_result.modified_count} modifiées."
            )
        if tool_result.errors:
            samples = [f"{error.file}: {error.message}" for error in tool_result.errors[:3]]
            message = f"{message} Erreurs: {' | '.join(samples)}"
        return message

    return f"{plan.user_reply} (résultat indisponible)"
