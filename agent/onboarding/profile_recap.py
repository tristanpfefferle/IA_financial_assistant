"""Shared onboarding profile recap builders."""

from __future__ import annotations

from datetime import datetime
from typing import Any


_FRENCH_MONTHS = {
    1: "janvier",
    2: "février",
    3: "mars",
    4: "avril",
    5: "mai",
    6: "juin",
    7: "juillet",
    8: "août",
    9: "septembre",
    10: "octobre",
    11: "novembre",
    12: "décembre",
}


def format_birth_date_fr_long(birth_date_iso: str) -> str:
    """Format an ISO birth date to French long format (10 mai 1995)."""

    try:
        parsed = datetime.strptime(str(birth_date_iso).strip(), "%Y-%m-%d")
    except ValueError:
        return str(birth_date_iso).strip()
    return f"{parsed.day} {_FRENCH_MONTHS[parsed.month]} {parsed.year}"


def build_profile_recap_reply(profile_fields: dict[str, Any]) -> str:
    """Build onboarding profile recap confirmation prompt."""

    first_name = str(profile_fields.get("first_name", "")).strip()
    last_name = str(profile_fields.get("last_name", "")).strip()
    birth_date_iso = str(profile_fields.get("birth_date", "")).strip()
    birth_date_display = format_birth_date_fr_long(birth_date_iso)
    return (
        f"Parfait ✅\n\nRécapitulatif de ton profil :\n- Prénom: {first_name}\n- Nom: {last_name}\n- Date de naissance: {birth_date_display}\n\n"
        "Est-ce bien correct ?"
    )
