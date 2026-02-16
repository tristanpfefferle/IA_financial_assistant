"""Profile field normalization helpers shared across agent and backend boundaries."""

from __future__ import annotations

import re

from shared.models import PROFILE_ALLOWED_FIELDS, ToolError, ToolErrorCode


_PROFILE_FIELD_ALIASES: dict[str, str] = {
    "prenom": "first_name",
    "prénom": "first_name",
    "nom": "last_name",
    "date de naissance": "birth_date",
    "naissance": "birth_date",
    "ne": "birth_date",
    "né": "birth_date",
    "nee": "birth_date",
    "née": "birth_date",
    "genre": "gender",
    "adresse": "address_line1",
    "adresse 1": "address_line1",
    "adresse 2": "address_line2",
    "complement d adresse": "address_line2",
    "complément d adresse": "address_line2",
    "code postal": "postal_code",
    "ville": "city",
    "canton": "canton",
    "pays": "country",
    "situation personnelle": "personal_situation",
    "situation professionnelle": "professional_situation",
}


def _normalize_token(raw_field: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", raw_field, flags=re.UNICODE)
    return " ".join(cleaned.lower().split())


def normalize_profile_field(raw_field: str) -> str | ToolError:
    """Map French profile field aliases to canonical column names."""

    normalized_raw_field = _normalize_token(raw_field)
    if normalized_raw_field in PROFILE_ALLOWED_FIELDS:
        return normalized_raw_field

    canonical = _PROFILE_FIELD_ALIASES.get(normalized_raw_field)
    if canonical is not None:
        return canonical

    return ToolError(
        code=ToolErrorCode.VALIDATION_ERROR,
        message="Champ de profil non reconnu.",
        details={"field": raw_field},
    )

