"""LLM-based merchant cleanup suggestions pipeline."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from shared import config as _config

logger = logging.getLogger(__name__)

_MAX_SUGGESTIONS = 50
_ALLOWED_ACTIONS = {"rename", "merge", "categorize", "keep"}


@dataclass(slots=True)
class MerchantSuggestion:
    """Structured merchant cleanup suggestion."""

    action: str
    source_merchant_id: UUID | None
    target_merchant_id: UUID | None
    suggested_name: str | None
    suggested_category: str | None
    confidence: float
    rationale: str
    sample_aliases: list[str]


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _parse_suggestion(raw: Any) -> MerchantSuggestion | None:
    if not isinstance(raw, dict):
        return None

    action = str(raw.get("action") or "").strip().lower()
    if action not in _ALLOWED_ACTIONS:
        return None

    def _to_uuid(value: Any) -> UUID | None:
        if value is None:
            return None
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    source_merchant_id = _to_uuid(raw.get("source_merchant_id"))
    target_merchant_id = _to_uuid(raw.get("target_merchant_id"))

    if action in {"rename", "categorize", "keep"} and source_merchant_id is None:
        return None
    if action == "merge" and (source_merchant_id is None or target_merchant_id is None):
        return None

    suggested_name_raw = raw.get("suggested_name")
    suggested_name = " ".join(str(suggested_name_raw).split()) if suggested_name_raw is not None else None
    if suggested_name == "":
        suggested_name = None

    suggested_category_raw = raw.get("suggested_category")
    suggested_category = (
        " ".join(str(suggested_category_raw).split()) if suggested_category_raw is not None else None
    )
    if suggested_category == "":
        suggested_category = None

    if action == "rename" and not suggested_name:
        return None

    sample_aliases_raw = raw.get("sample_aliases")
    sample_aliases: list[str] = []
    if isinstance(sample_aliases_raw, list):
        for alias in sample_aliases_raw:
            cleaned_alias = " ".join(str(alias).split())
            if cleaned_alias:
                sample_aliases.append(cleaned_alias)

    return MerchantSuggestion(
        action=action,
        source_merchant_id=source_merchant_id,
        target_merchant_id=target_merchant_id,
        suggested_name=suggested_name,
        suggested_category=suggested_category,
        confidence=_clamp_confidence(raw.get("confidence")),
        rationale=" ".join(str(raw.get("rationale") or "").split())[:500],
        sample_aliases=sample_aliases[:10],
    )


def parse_cleanup_suggestions(payload: Any) -> list[MerchantSuggestion]:
    """Parse LLM payload into validated suggestions without raising."""

    if not isinstance(payload, dict):
        return []

    raw_suggestions = payload.get("suggestions")
    if not isinstance(raw_suggestions, list):
        return []

    parsed: list[MerchantSuggestion] = []
    for raw_suggestion in raw_suggestions[:_MAX_SUGGESTIONS]:
        suggestion = _parse_suggestion(raw_suggestion)
        if suggestion is None:
            continue
        parsed.append(suggestion)
    return parsed


def _build_cleanup_prompt(*, merchants: list[dict[str, Any]]) -> str:
    compact_merchants = [
        {
            "id": str(merchant.get("id")),
            "name": merchant.get("name"),
            "name_norm": merchant.get("name_norm"),
            "aliases": merchant.get("aliases") if isinstance(merchant.get("aliases"), list) else [],
            "category": merchant.get("category"),
        }
        for merchant in merchants
    ]
    return (
        "Tu es un assistant de nettoyage de marchands financiers. "
        "Retourne UNIQUEMENT un JSON valide avec la structure demandée. "
        "Ne retourne aucun markdown.\n"
        "Objectif: proposer max 50 suggestions rename|merge|categorize|keep.\n"
        "Règles: merge seulement si très probable, rename stable/humain, "
        "categorize seulement si catégorie vide ou incohérente.\n"
        "Format attendu: {\"suggestions\":[{"
        "\"action\":\"rename|merge|categorize|keep\","
        "\"source_merchant_id\":\"uuid\","
        "\"target_merchant_id\":\"uuid or null\","
        "\"suggested_name\":\"string or null\","
        "\"suggested_category\":\"string or null\","
        "\"confidence\":0.0,"
        "\"rationale\":\"short string\","
        "\"sample_aliases\":[\"...\"]}]}.\n"
        f"Marchands: {json.dumps(compact_merchants, ensure_ascii=False)}"
    )


def _call_llm_json(prompt: str) -> dict[str, Any]:
    api_key = _config.openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")

    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=20.0)
    response = client.chat.completions.create(
        model=_config.llm_model(),
        messages=[
            {"role": "system", "content": "Tu réponds toujours avec du JSON strict."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content if response.choices else None
    if not content:
        return {}
    return json.loads(content)


def run_merchant_cleanup(*, profile_id: UUID, profiles_repository: Any) -> list[MerchantSuggestion]:
    """Run merchant cleanup suggestions through LLM and parse robustly."""

    try:
        merchants = profiles_repository.list_merchants(profile_id=profile_id, limit=5000)
    except Exception:
        logger.exception("merchant_cleanup_list_merchants_failed profile_id=%s", profile_id)
        return []

    if not merchants:
        return []

    prompt = _build_cleanup_prompt(merchants=merchants)
    try:
        llm_payload = _call_llm_json(prompt)
    except Exception:
        logger.exception("merchant_cleanup_llm_failed profile_id=%s", profile_id)
        return []

    try:
        return parse_cleanup_suggestions(llm_payload)
    except Exception:
        logger.exception("merchant_cleanup_parse_failed profile_id=%s", profile_id)
        return []
