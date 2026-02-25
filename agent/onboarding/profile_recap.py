"""Shared onboarding profile recap builders."""

from __future__ import annotations

from typing import Any


def build_profile_recap_reply(profile_fields: dict[str, Any]) -> str:
    """Build onboarding profile recap confirmation prompt."""

    first_name = str(profile_fields.get("first_name", "")).strip()
    last_name = str(profile_fields.get("last_name", "")).strip()
    birth_date_iso = str(profile_fields.get("birth_date", "")).strip()
    return (
        f"Parfait ✅\n\nRécapitulatif de ton profil :\n- Prénom: {first_name}\n- Nom: {last_name}\n- Date de naissance: {birth_date_iso}\n\n"
        "Tout est correct ?"
    )

