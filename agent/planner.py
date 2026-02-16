"""Deterministic planning for user messages (LLM-free for now)."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from shared.models import ToolError, ToolErrorCode
from shared.profile_fields import normalize_profile_field

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
_MONTH_YEAR_LINK_TOKENS = {"en", "et", "puis", "ainsi", "que"}

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
_PROFILE_FIELD_WHITELIST = {
    "first_name",
    "last_name",
    "birth_date",
    "gender",
    "address_line1",
    "address_line2",
    "postal_code",
    "city",
    "canton",
    "country",
    "personal_situation",
    "professional_situation",
}
_PROFILE_CORE_FIELDS = [
    "first_name",
    "last_name",
    "birth_date",
    "gender",
    "address_line1",
    "address_line2",
    "postal_code",
    "city",
    "canton",
    "country",
]
_PROFILE_ADDRESS_FIELDS = ["address_line1", "address_line2", "postal_code", "city", "canton", "country"]


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


def _extract_month_year_pairs(message: str) -> list[tuple[int, int | None]]:
    """Extract ordered `(month, year)` pairs from a natural-language message."""

    lower_message = message.lower()
    tokenized_matches = list(re.finditer(r"[\wéèêëàâäùûüôöîïç\.]+", lower_message))
    month_lookup = {
        **_FRENCH_MONTHS,
        **{alias.strip("."): value for alias, value in _FRENCH_MONTH_ALIASES.items()},
    }
    month_year_pairs: list[tuple[int, int | None]] = []
    year_pattern = re.compile(r"(19\d{2}|20\d{2}|21\d{2})")

    for index, match in enumerate(tokenized_matches):
        normalized_token = match.group(0).strip(".")
        if normalized_token in _MONTH_YEAR_LINK_TOKENS:
            continue

        month = month_lookup.get(normalized_token)
        if month is None:
            continue

        year: int | None = None
        lookahead_index = index + 1
        while lookahead_index < len(tokenized_matches):
            next_token = tokenized_matches[lookahead_index].group(0).strip(".")
            if next_token in _MONTH_YEAR_LINK_TOKENS:
                lookahead_index += 1
                continue
            if year_pattern.fullmatch(next_token):
                year = int(next_token)
            break

        month_year_pairs.append((month, year))

    return month_year_pairs


def _extract_year(message: str) -> int | None:
    years = re.findall(r"\b(19\d{2}|20\d{2}|21\d{2})\b", message)
    if not years:
        return None
    return int(years[0])


def _extract_merchant_name(message: str) -> str | None:
    """Extract merchant mention from phrases like `chez coop`.

    Temporal complements (e.g. `en janvier 2026`) are removed from the merchant segment
    so filters can be composed into a single tool payload.
    """

    match = re.search(r"\bchez\s+(?P<merchant>.+)$", message, flags=re.IGNORECASE)
    if match is None:
        return None

    merchant_value = match.group("merchant").strip(" .,!?:;\"'")
    if not merchant_value:
        return None

    month_tokens = sorted(
        {**_FRENCH_MONTHS, **{alias.strip("."): value for alias, value in _FRENCH_MONTH_ALIASES.items()}},
        key=len,
        reverse=True,
    )
    month_pattern = "|".join(re.escape(token) for token in month_tokens)
    temporal_pattern = (
        r"\s+(?:"
        rf"en\s+(?:{month_pattern})(?:\s+(?:19\d{{2}}|20\d{{2}}|21\d{{2}}))?"
        rf"(?:\s*,\s*(?:{month_pattern})(?:\s+(?:19\d{{2}}|20\d{{2}}|21\d{{2}}))?)*"
        rf"(?:\s+et\s+(?:en\s+)?(?:{month_pattern})(?:\s+(?:19\d{{2}}|20\d{{2}}|21\d{{2}}))?)?|"
        r"(?:ces|les)\s+\d+\s+derniers?\s+mois|"
        r"ce\s+mois-ci|"
        r"le\s+mois\s+dernier"
        r")\s*$"
    )
    merchant_without_temporal = re.sub(temporal_pattern, "", merchant_value, flags=re.IGNORECASE)
    merchant_name = merchant_without_temporal.strip(" .,!?:;\"'")
    return merchant_name or None


def _resolve_two_month_period(
    month_year_pairs: list[tuple[int, int | None]],
) -> tuple[date, date] | None:
    if len(month_year_pairs) < 2:
        return None

    anchor_year = next((year for _, year in month_year_pairs if year is not None), None)
    if anchor_year is None:
        return None

    resolved_pairs: list[tuple[int, int]] = []
    current_year = anchor_year
    previous_month: int | None = None

    for month, year in month_year_pairs:
        if year is not None:
            current_year = year
        elif previous_month is not None and month < previous_month:
            current_year += 1

        resolved_pairs.append((month, current_year))
        previous_month = month

    boundaries = [
        (
            date(year, month, 1),
            date(year, month, calendar.monthrange(year, month)[1]),
        )
        for month, year in resolved_pairs
    ]
    start = min(boundary[0] for boundary in boundaries)
    end = max(boundary[1] for boundary in boundaries)
    return start, end


def _shift_month(month_anchor: date, month_delta: int) -> date:
    """Return the first day of month shifted by `month_delta` months."""

    month_index = month_anchor.year * 12 + (month_anchor.month - 1) + month_delta
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _extract_relative_month_range(message: str, today: date) -> tuple[date, date] | None:
    lower_message = message.lower()

    relative_months_match = re.search(
        r"\b(?:ces|les)\s+(?P<n>\d+)\s+derniers?\s+mois\b",
        lower_message,
    )
    if relative_months_match is not None:
        months_count = int(relative_months_match.group("n"))
        if months_count <= 0:
            return None
        month_anchor = date(today.year, today.month, 1)
        # Convention: "N derniers mois" commence au 1er jour du mois obtenu en
        # reculant de N mois depuis aujourd'hui, et se termine à aujourd'hui.
        # Exemples:
        # - today=2026-02-16, N=2 -> start=2025-12-01, end=2026-02-16
        # - today=2026-01-10, N=1 -> start=2025-12-01, end=2026-01-10
        start_date = _shift_month(month_anchor, -months_count)
        return start_date, today

    if re.search(r"\bce\s+mois-ci\b", lower_message):
        return date(today.year, today.month, 1), today

    if re.search(r"\ble\s+mois\s+dernier\b", lower_message):
        previous_month_start = _shift_month(date(today.year, today.month, 1), -1)
        previous_month_end = date(
            previous_month_start.year,
            previous_month_start.month,
            calendar.monthrange(previous_month_start.year, previous_month_start.month)[1],
        )
        return previous_month_start, previous_month_end

    return None


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


def _strip_terminal_punctuation(value: str) -> str:
    return value.strip().strip(" .,!?:;\"'")


def _build_profile_fields_request(fields: list[str]) -> ToolCallPlan:
    filtered_fields = [field for field in fields if field in _PROFILE_FIELD_WHITELIST]
    return ToolCallPlan(
        tool_name="finance_profile_get",
        payload={"fields": filtered_fields},
        user_reply="Voici vos informations de profil.",
    )


def _build_profile_update_request(fields_to_update: dict[str, object]) -> ToolCallPlan:
    sanitized_set = {key: value for key, value in fields_to_update.items() if key in _PROFILE_FIELD_WHITELIST}
    return ToolCallPlan(
        tool_name="finance_profile_update",
        payload={"set": sanitized_set},
        user_reply="Profil mis à jour.",
    )


def _try_build_profile_plan(message: str) -> ToolCallPlan | ErrorPlan | None:
    lower_message = message.lower()

    if re.search(r"\bquel\s+est\s+mon\s+pr[ée]nom\b", lower_message):
        return _build_profile_fields_request(["first_name"])

    if re.search(r"\bquel\s+est\s+mon\s+nom\b", lower_message):
        return _build_profile_fields_request(["last_name"])

    if re.search(r"\bquelle\s+est\s+ma\s+date\s+de\s+naissance\b", lower_message):
        return _build_profile_fields_request(["birth_date"])

    if re.search(r"\bquelle\s+est\s+mon\s+adresse\b", lower_message):
        return _build_profile_fields_request(_PROFILE_ADDRESS_FIELDS)

    if re.search(r"\bmontre\s+mes\s+infos\s+perso\b", lower_message):
        return _build_profile_fields_request(_PROFILE_CORE_FIELDS)

    if re.search(r"\b(?:supprime|efface)\s+mon\s+pr[ée]nom\b", lower_message):
        return _build_profile_update_request({"first_name": None})

    first_name_match = re.search(
        r"\b(?:mon\s+pr[ée]nom\s+est|mets?\s+mon\s+pr[ée]nom\s+[àa])\s+(?P<first_name>.+)$",
        message,
        flags=re.IGNORECASE,
    )
    if first_name_match is not None:
        first_name = _strip_terminal_punctuation(first_name_match.group("first_name"))
        if first_name:
            return _build_profile_update_request({"first_name": first_name})

    full_name_match = re.search(r"\bje\s+m[\"'’]?appelle\s+(?P<full_name>.+)$", message, flags=re.IGNORECASE)
    if full_name_match is not None:
        full_name = _strip_terminal_punctuation(full_name_match.group("full_name"))
        name_tokens = [token for token in full_name.split() if token]
        if name_tokens:
            first_name = name_tokens[0]
            last_name = " ".join(name_tokens[1:]) if len(name_tokens) > 1 else None
            profile_set: dict[str, object] = {"first_name": first_name}
            if last_name is not None:
                profile_set["last_name"] = last_name
            return _build_profile_update_request(profile_set)

    birth_date_match = re.search(
        r"\b(?:je\s+suis\s+n[ée]\s+le|ma\s+date\s+de\s+naissance\s+est)\s+(?P<birth_date>\d{4}-\d{2}-\d{2})\b",
        lower_message,
    )
    if birth_date_match is not None:
        birth_date_raw = birth_date_match.group("birth_date")
        try:
            parsed_birth_date = date.fromisoformat(birth_date_raw)
        except ValueError:
            return None
        return _build_profile_update_request({"birth_date": parsed_birth_date.isoformat()})

    generic_field_plan = _try_build_profile_get_field_plan(message)
    if generic_field_plan is not None:
        return generic_field_plan

    return None


def _try_build_profile_get_field_plan(message: str) -> ToolCallPlan | ErrorPlan | None:
    match = re.search(
        r"\bquel(?:le)?\s+est\s+m(?:on|a)\s+(?P<field>[\w\s\-éèêàùâîôç]+)\b",
        message,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None

    raw_field = _strip_terminal_punctuation(match.group("field"))
    if not raw_field:
        return ErrorPlan(
            reply="Je n’ai pas compris quelle info du profil vous voulez (prénom, nom, ville, etc.).",
            tool_error=ToolError(
                code=ToolErrorCode.VALIDATION_ERROR,
                message="Champ de profil non précisé.",
            ),
        )

    normalized_field = normalize_profile_field(raw_field)
    if isinstance(normalized_field, ToolError):
        return ErrorPlan(
            reply="Je n’ai pas compris quelle info du profil vous voulez (prénom, nom, ville, etc.).",
            tool_error=normalized_field,
        )

    return _build_profile_fields_request([normalized_field])


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

    profile_plan = _try_build_profile_plan(normalized_message)
    if profile_plan is not None:
        return profile_plan

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
        merchant_name = _extract_merchant_name(normalized_message)
        today = _today()

        month_year_pairs = _extract_month_year_pairs(normalized_message)
        multi_month_range = _resolve_two_month_period(month_year_pairs)
        if multi_month_range is not None:
            start_date, end_date = multi_month_range
            payload: dict[str, object] = {
                "direction": "DEBIT_ONLY",
                "date_range": {
                    "start_date": start_date,
                    "end_date": end_date,
                },
            }
            if merchant_name is not None:
                payload["merchant"] = merchant_name
            return ToolCallPlan(
                tool_name="finance_releves_sum",
                payload=payload,
                user_reply="OK, je calcule le total de vos dépenses.",
            )

        relative_month_range = _extract_relative_month_range(normalized_message, today)
        if relative_month_range is not None:
            start_date, end_date = relative_month_range
            payload = {
                "direction": "DEBIT_ONLY",
                "date_range": {
                    "start_date": start_date,
                    "end_date": end_date,
                },
            }
            if merchant_name is not None:
                payload["merchant"] = merchant_name
            return ToolCallPlan(
                tool_name="finance_releves_sum",
                payload=payload,
                user_reply="OK, je calcule le total de vos dépenses.",
            )

        month = _extract_month(lower_message)
        if month is not None:
            explicit_year = _extract_year(normalized_message)
            year = explicit_year or today.year
            if explicit_year is None and month > today.month:
                return ClarificationPlan(question="De quelle année parlez-vous ?")
            last_day = calendar.monthrange(year, month)[1]
            payload: dict[str, object] = {
                "direction": "DEBIT_ONLY",
                "date_range": {
                    "start_date": date(year, month, 1),
                    "end_date": date(year, month, last_day),
                },
            }
            if merchant_name is not None:
                payload["merchant"] = merchant_name
            return ToolCallPlan(
                tool_name="finance_releves_sum",
                payload=payload,
                user_reply="OK, je calcule le total de vos dépenses.",
            )

        if merchant_name is not None:
            return ToolCallPlan(
                tool_name="finance_releves_sum",
                payload={
                    "direction": "DEBIT_ONLY",
                    "merchant": merchant_name,
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
