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


def _parse_suggestion_with_reason(raw: Any) -> tuple[MerchantSuggestion | None, str | None]:
    """Parse one suggestion and return a compact rejection reason when invalid."""

    if not isinstance(raw, dict):
        return None, "invalid_item"

    action = str(raw.get("action") or "").strip().lower()
    if action not in _ALLOWED_ACTIONS:
        return None, "invalid_action"

    source_value = raw.get("source_merchant_id")
    target_value = raw.get("target_merchant_id")

    def _safe_uuid(value: Any) -> UUID | None:
        if value is None:
            return None
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    source_uuid = _safe_uuid(source_value)
    target_uuid = _safe_uuid(target_value)
    if source_value is not None and source_uuid is None:
        return None, "invalid_uuid"
    if target_value is not None and target_uuid is None:
        return None, "invalid_uuid"

    if action in {"rename", "categorize", "keep"} and source_uuid is None:
        return None, "missing_ids"
    if action == "merge" and (source_uuid is None or target_uuid is None):
        return None, "missing_ids"

    if action == "rename":
        candidate_name = " ".join(str(raw.get("suggested_name") or "").split())
        if not candidate_name:
            return None, "missing_suggested_name"

    return _parse_suggestion(raw), None


def parse_cleanup_suggestions_with_stats(payload: Any) -> tuple[list[MerchantSuggestion], dict[str, Any]]:
    """Parse LLM payload into suggestions and parsing diagnostics."""

    stats: dict[str, Any] = {
        "raw_count": 0,
        "parsed_count": 0,
        "rejected_count": 0,
        "rejected_reasons": {},
    }
    if not isinstance(payload, dict):
        return [], stats

    raw_suggestions = payload.get("suggestions")
    if not isinstance(raw_suggestions, list):
        return [], stats

    parsed: list[MerchantSuggestion] = []
    reason_counts: dict[str, int] = {}
    for raw_suggestion in raw_suggestions[:_MAX_SUGGESTIONS]:
        suggestion, reason = _parse_suggestion_with_reason(raw_suggestion)
        if suggestion is None:
            if reason:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            continue
        parsed.append(suggestion)

    stats["raw_count"] = min(len(raw_suggestions), _MAX_SUGGESTIONS)
    stats["parsed_count"] = len(parsed)
    stats["rejected_count"] = stats["raw_count"] - stats["parsed_count"]
    stats["rejected_reasons"] = reason_counts
    return parsed, stats


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
        "Contrainte stricte: Pour TOUT item, y compris action=keep, tu DOIS fournir source_merchant_id (uuid).\n"
        "N'utilise jamais merchant_id; utilise toujours source_merchant_id/target_merchant_id.\n"
        "Exemple minimal valide: {\"suggestions\":[{\"action\":\"keep\",\"source_merchant_id\":\"11111111-1111-1111-1111-111111111111\",\"target_merchant_id\":null,\"suggested_name\":null,\"suggested_category\":null,\"confidence\":0.99,\"rationale\":\"already clean\",\"sample_aliases\":[\"MIGROS\"]}]}.\n"
        f"Marchands: {json.dumps(compact_merchants, ensure_ascii=False)}"
    )


def _call_llm_json(prompt: str) -> tuple[dict[str, Any], str | None, dict[str, int]]:
    api_key = _config.openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")

    try:
        from openai import OpenAI
    except ImportError as exc:
        logger.warning("openai_sdk_missing")
        raise RuntimeError("OpenAI SDK unavailable") from exc

    client = OpenAI(api_key=api_key, timeout=20.0)
    response = client.chat.completions.create(
        model=_config.llm_model(),
        messages=[
            {"role": "system", "content": "Tu réponds toujours avec du JSON strict."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )

    llm_run_id = str(response.id) if getattr(response, "id", None) else None
    usage_dict: dict[str, int] = {}
    usage = getattr(response, "usage", None)
    if usage is not None:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if isinstance(prompt_tokens, int):
            usage_dict["prompt_tokens"] = prompt_tokens
        if isinstance(completion_tokens, int):
            usage_dict["completion_tokens"] = completion_tokens
        if isinstance(total_tokens, int):
            usage_dict["total_tokens"] = total_tokens

    content = response.choices[0].message.content if response.choices else None
    if not content:
        return {}, llm_run_id, usage_dict
    return json.loads(content), llm_run_id, usage_dict


def run_merchant_cleanup(
    *,
    profile_id: UUID,
    profiles_repository: Any,
    merchants: list[dict[str, Any]] | None = None,
) -> tuple[list[MerchantSuggestion], str | None, dict[str, int], dict[str, Any]]:
    """Run merchant cleanup suggestions through LLM and parse robustly."""

    if merchants is None:
        try:
            merchants = profiles_repository.list_merchants(profile_id=profile_id, limit=5000)
        except Exception:
            logger.exception("merchant_cleanup_list_merchants_failed profile_id=%s", profile_id)
            return [], None, {}, {"raw_count": 0, "parsed_count": 0, "rejected_count": 0, "rejected_reasons": {}}

    if not merchants:
        return [], None, {}, {"raw_count": 0, "parsed_count": 0, "rejected_count": 0, "rejected_reasons": {}}

    prompt = _build_cleanup_prompt(merchants=merchants)
    try:
        llm_payload, llm_run_id, usage = _call_llm_json(prompt)
    except Exception:
        logger.exception("merchant_cleanup_llm_failed profile_id=%s", profile_id)
        return [], None, {}, {"raw_count": 0, "parsed_count": 0, "rejected_count": 0, "rejected_reasons": {}}

    raw_suggestions = llm_payload.get("suggestions") if isinstance(llm_payload, dict) else None
    suggestions_count = len(raw_suggestions) if isinstance(raw_suggestions, list) else 0
    logger.info(
        "merchant_cleanup_llm_ok profile_id=%s llm_run_id=%s usage=%s merchants_count=%s prompt_chars=%s",
        profile_id,
        llm_run_id,
        usage,
        suggestions_count,
        len(prompt),
    )
    if not isinstance(raw_suggestions, list):
        excerpt = json.dumps(llm_payload, ensure_ascii=False)[:500]
        logger.warning(
            'merchant_cleanup_llm_invalid_shape profile_id=%s llm_run_id=%s excerpt="%s"',
            profile_id,
            llm_run_id,
            excerpt,
        )

    try:
        suggestions, stats = parse_cleanup_suggestions_with_stats(llm_payload)
        logger.info("merchant_cleanup_parse_stats profile_id=%s llm_run_id=%s stats=%s", profile_id, llm_run_id, stats)
        return suggestions, llm_run_id, usage, stats
    except Exception:
        logger.exception("merchant_cleanup_parse_failed profile_id=%s", profile_id)
        return [], llm_run_id, usage, {"raw_count": 0, "parsed_count": 0, "rejected_count": 0, "rejected_reasons": {}}
