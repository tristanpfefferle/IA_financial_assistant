"""Batch resolver for merchant map_alias suggestions using LLM decisions."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from shared import config as _config

logger = logging.getLogger(__name__)

_CANONICAL_CATEGORY_LABELS: dict[str, str] = {
    "food": "Alimentation",
    "housing": "Logement",
    "transport": "Transport",
    "health": "Santé",
    "leisure": "Loisirs",
    "shopping": "Shopping",
    "bills": "Factures",
    "taxes": "Impôts",
    "insurance": "Assurance",
    "other": "Autres",
}
_ALLOWED_CATEGORY_KEYS = set(_CANONICAL_CATEGORY_LABELS)


def _normalize_text(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value.strip().lower()).encode("ascii", "ignore").decode("ascii")
    return " ".join(normalized.split())


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _compact_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300] or exc.__class__.__name__


def _build_batch_prompt(*, items: list[dict[str, str]]) -> str:
    categories = [{"system_key": key, "label": label} for key, label in _CANONICAL_CATEGORY_LABELS.items()]
    return (
        "Tu résous des alias marchands bancaires. Réponds UNIQUEMENT avec un JSON valide strict, sans markdown.\n"
        "Format STRICT attendu:\n"
        '{"resolutions":[{"suggestion_id":"uuid","action":"link_existing|create_entity",'
        '"merchant_entity_id":"uuid|null","canonical_name":"string|null",'
        '"canonical_name_norm":"string|null","country":"CH",'
        '"suggested_category_norm":"food|housing|transport|health|leisure|shopping|bills|taxes|insurance|other",'
        '"suggested_category_label":"Alimentation|Logement|Transport|Santé|Loisirs|Shopping|Factures|Impôts|Assurance|Autres",'
        '"confidence":0.0,"rationale":"short"}]}\n'
        "Règles: action=link_existing => merchant_entity_id obligatoire; canonical_name/canonical_name_norm peuvent être null.\n"
        "Règles: action=create_entity => merchant_entity_id doit être null; canonical_name et canonical_name_norm obligatoires (lowercase/trim).\n"
        "suggested_category_norm DOIT être une valeur canonique parmi la liste fournie.\n"
        f"Catégories canoniques: {json.dumps(categories, ensure_ascii=False)}\n"
        f"Suggestions à résoudre: {json.dumps(items, ensure_ascii=False)}"
    )


def _call_llm_json(prompt: str) -> tuple[dict[str, Any], str | None, dict[str, int]]:
    api_key = _config.openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")

    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=30.0)
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
        for token_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, token_name, None)
            if isinstance(value, int):
                usage_dict[token_name] = value

    content = response.choices[0].message.content if response.choices else None
    if not content:
        return {}, llm_run_id, usage_dict
    return json.loads(content), llm_run_id, usage_dict


def _validate_resolution(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw, dict):
        return None, "invalid_item"

    suggestion_raw = raw.get("suggestion_id")
    try:
        suggestion_id = UUID(str(suggestion_raw))
    except (TypeError, ValueError):
        return None, "invalid_suggestion_id"

    action = str(raw.get("action") or "").strip()
    if action not in {"link_existing", "create_entity"}:
        return None, "invalid_action"

    merchant_entity_id: UUID | None = None
    merchant_entity_raw = raw.get("merchant_entity_id")
    if merchant_entity_raw is not None:
        try:
            merchant_entity_id = UUID(str(merchant_entity_raw))
        except (TypeError, ValueError):
            return None, "invalid_merchant_entity_id"

    canonical_name = " ".join(str(raw.get("canonical_name") or "").split()) or None
    canonical_name_norm = _normalize_text(str(raw.get("canonical_name_norm") or "")) or None
    country = str(raw.get("country") or "CH").strip().upper() or "CH"

    suggested_category_norm = _normalize_text(str(raw.get("suggested_category_norm") or ""))
    if suggested_category_norm not in _ALLOWED_CATEGORY_KEYS:
        return None, "invalid_suggested_category_norm"

    suggested_category_label = " ".join(
        str(raw.get("suggested_category_label") or _CANONICAL_CATEGORY_LABELS[suggested_category_norm]).split()
    )
    confidence = _clamp_confidence(raw.get("confidence"))
    rationale = " ".join(str(raw.get("rationale") or "").split())[:500]

    if action == "link_existing" and merchant_entity_id is None:
        return None, "missing_merchant_entity_id"
    if action == "create_entity" and (merchant_entity_id is not None or not canonical_name or not canonical_name_norm):
        return None, "invalid_create_entity_fields"

    return {
        "suggestion_id": suggestion_id,
        "action": action,
        "merchant_entity_id": merchant_entity_id,
        "canonical_name": canonical_name,
        "canonical_name_norm": canonical_name_norm,
        "country": country,
        "suggested_category_norm": suggested_category_norm,
        "suggested_category_label": suggested_category_label,
        "confidence": confidence,
        "rationale": rationale,
    }, None


def resolve_pending_map_alias(*, profile_id: UUID, profiles_repository: Any, limit: int) -> dict[str, Any]:
    """Resolve pending/failed map_alias suggestions in one LLM batch call."""

    stats: dict[str, Any] = {
        "processed": 0,
        "applied": 0,
        "created_entities": 0,
        "linked_aliases": 0,
        "updated_transactions": 0,
        "failed": 0,
        "llm_run_id": None,
        "usage": {},
        "warnings": [],
    }

    suggestions = profiles_repository.list_map_alias_suggestions(profile_id=profile_id, limit=max(1, int(limit)))
    if not suggestions:
        return stats

    categories_payload = [
        {"system_key": key, "name": label}
        for key, label in _CANONICAL_CATEGORY_LABELS.items()
    ]
    profiles_repository.ensure_system_categories(profile_id=profile_id, categories=categories_payload)
    category_rows = profiles_repository.list_profile_categories(profile_id=profile_id)

    categories_by_key: dict[str, UUID] = {}
    for row in category_rows:
        category_id_raw = row.get("id")
        if category_id_raw is None:
            continue
        try:
            category_id = UUID(str(category_id_raw))
        except (TypeError, ValueError):
            continue

        for candidate in (row.get("system_key"), row.get("name_norm")):
            normalized = _normalize_text(str(candidate or ""))
            if normalized:
                categories_by_key[normalized] = category_id

    suggestions_by_id: dict[UUID, dict[str, Any]] = {}
    llm_items: list[dict[str, str]] = []
    for item in suggestions:
        suggestion_id_raw = item.get("id")
        observed_alias = " ".join(str(item.get("observed_alias") or "").split())
        observed_alias_norm = _normalize_text(str(item.get("observed_alias_norm") or ""))
        if not observed_alias:
            observed_alias = " ".join(str(item.get("observed_alias_norm") or "").split())
        if not observed_alias or not observed_alias_norm:
            continue
        try:
            suggestion_id = UUID(str(suggestion_id_raw))
        except (TypeError, ValueError):
            continue
        suggestions_by_id[suggestion_id] = {
            "id": suggestion_id,
            "observed_alias": observed_alias,
            "observed_alias_norm": observed_alias_norm,
        }
        llm_items.append(
            {
                "suggestion_id": str(suggestion_id),
                "observed_alias": observed_alias,
                "observed_alias_norm": observed_alias_norm,
            }
        )

    if not llm_items:
        return stats

    prompt = _build_batch_prompt(items=llm_items)
    llm_payload, llm_run_id, usage = _call_llm_json(prompt)
    stats["llm_run_id"] = llm_run_id
    stats["usage"] = usage

    raw_resolutions = llm_payload.get("resolutions") if isinstance(llm_payload, dict) else None
    if not isinstance(raw_resolutions, list):
        raw_resolutions = []
        stats["warnings"].append("invalid_llm_payload")

    seen_ids: set[UUID] = set()
    for raw_resolution in raw_resolutions:
        parsed, reason = _validate_resolution(raw_resolution)
        suggestion_id = None
        if isinstance(raw_resolution, dict):
            try:
                suggestion_id = UUID(str(raw_resolution.get("suggestion_id")))
            except (TypeError, ValueError):
                suggestion_id = None

        if parsed is None:
            if suggestion_id and suggestion_id in suggestions_by_id:
                stats["processed"] += 1
                stats["failed"] += 1
                seen_ids.add(suggestion_id)
                profiles_repository.update_merchant_suggestion_after_resolve(
                    profile_id=profile_id,
                    suggestion_id=suggestion_id,
                    status="failed",
                    error=reason,
                    llm_model=_config.llm_model(),
                    llm_run_id=llm_run_id,
                    confidence=0.0,
                    rationale=reason,
                    target_merchant_entity_id=None,
                    suggested_entity_name=None,
                    suggested_entity_name_norm=None,
                    suggested_category_norm=None,
                    suggested_category_label=None,
                )
            continue

        resolution = parsed
        suggestion_id = resolution["suggestion_id"]
        suggestion = suggestions_by_id.get(suggestion_id)
        if suggestion is None:
            continue

        seen_ids.add(suggestion_id)
        stats["processed"] += 1
        merchant_entity_id: UUID | None = resolution["merchant_entity_id"]
        try:
            if resolution["action"] == "create_entity":
                entity = profiles_repository.create_merchant_entity(
                    canonical_name=resolution["canonical_name"],
                    canonical_name_norm=resolution["canonical_name_norm"],
                    country=resolution["country"],
                    suggested_category_norm=resolution["suggested_category_norm"],
                    suggested_category_label=resolution["suggested_category_label"],
                    suggested_confidence=resolution["confidence"],
                    suggested_source="llm",
                )
                merchant_entity_id = UUID(str(entity["id"]))
                stats["created_entities"] += 1

            if merchant_entity_id is None:
                raise ValueError("missing merchant_entity_id")

            profiles_repository.upsert_merchant_alias(
                merchant_entity_id=merchant_entity_id,
                alias=suggestion["observed_alias"],
                alias_norm=suggestion["observed_alias_norm"],
                source="llm",
            )
            stats["linked_aliases"] += 1

            category_id = categories_by_key.get(resolution["suggested_category_norm"])
            if category_id is None:
                category_id = categories_by_key.get(_normalize_text(resolution["suggested_category_label"]))
            if category_id is not None:
                profiles_repository.upsert_profile_merchant_override(
                    profile_id=profile_id,
                    merchant_entity_id=merchant_entity_id,
                    category_id=category_id,
                    status="auto",
                )

            updated_transactions = profiles_repository.apply_entity_to_profile_transactions(
                profile_id=profile_id,
                observed_alias_norm=suggestion["observed_alias_norm"],
                merchant_entity_id=merchant_entity_id,
                category_id=category_id,
            )
            stats["updated_transactions"] += int(updated_transactions or 0)

            profiles_repository.update_merchant_suggestion_after_resolve(
                profile_id=profile_id,
                suggestion_id=suggestion_id,
                status="applied",
                error=None,
                llm_model=_config.llm_model(),
                llm_run_id=llm_run_id,
                confidence=resolution["confidence"],
                rationale=resolution["rationale"],
                target_merchant_entity_id=merchant_entity_id,
                suggested_entity_name=resolution["canonical_name"],
                suggested_entity_name_norm=resolution["canonical_name_norm"],
                suggested_category_norm=resolution["suggested_category_norm"],
                suggested_category_label=resolution["suggested_category_label"],
            )
            stats["applied"] += 1
        except Exception as exc:
            stats["failed"] += 1
            profiles_repository.update_merchant_suggestion_after_resolve(
                profile_id=profile_id,
                suggestion_id=suggestion_id,
                status="failed",
                error=_compact_error(exc),
                llm_model=_config.llm_model(),
                llm_run_id=llm_run_id,
                confidence=resolution["confidence"],
                rationale=resolution["rationale"],
                target_merchant_entity_id=merchant_entity_id,
                suggested_entity_name=resolution["canonical_name"],
                suggested_entity_name_norm=resolution["canonical_name_norm"],
                suggested_category_norm=resolution["suggested_category_norm"],
                suggested_category_label=resolution["suggested_category_label"],
            )

    for suggestion_id in suggestions_by_id:
        if suggestion_id in seen_ids:
            continue
        stats["processed"] += 1
        stats["failed"] += 1
        profiles_repository.update_merchant_suggestion_after_resolve(
            profile_id=profile_id,
            suggestion_id=suggestion_id,
            status="failed",
            error="missing_llm_resolution",
            llm_model=_config.llm_model(),
            llm_run_id=llm_run_id,
            confidence=0.0,
            rationale="missing_llm_resolution",
            target_merchant_entity_id=None,
            suggested_entity_name=None,
            suggested_entity_name_norm=None,
            suggested_category_norm=None,
            suggested_category_label=None,
        )

    logger.info(
        "map_alias_resolve_done profile_id=%s llm_run_id=%s usage=%s stats=%s",
        profile_id,
        llm_run_id,
        usage,
        stats,
    )
    return stats
