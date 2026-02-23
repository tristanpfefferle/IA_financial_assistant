"""Backfill `releves_bancaires.category_id` with deterministic classification priority."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from backend.db.supabase_client import SupabaseClient, SupabaseSettings
from backend.repositories.profiles_repository import SupabaseProfilesRepository
from backend.services.classification.decision_engine import decide_releve_classification, normalize_merchant_alias
from backend.services.releves_import.importer import RelevesImportService
from shared import config


BATCH_SIZE = 500


class _NoopRelevesRepository:
    pass


def _build_client() -> SupabaseClient:
    url = config.supabase_url()
    service_key = config.supabase_service_role_key()
    anon_key = config.supabase_anon_key()
    if not url or not service_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")
    return SupabaseClient(settings=SupabaseSettings(url=url, service_role_key=service_key, anon_key=anon_key))


def run() -> None:
    client = _build_client()
    profiles_repository = SupabaseProfilesRepository(client=client)
    import_service = RelevesImportService(
        releves_repository=_NoopRelevesRepository(),  # type: ignore[arg-type]
        profiles_repository=profiles_repository,
    )

    offset = 0
    updated_count = 0

    while True:
        rows, _ = client.get_rows(
            table="releves_bancaires",
            query={
                "select": "id,profile_id,bank_account_id,date,libelle,payee,montant,devise,metadonnees,merchant_entity_id",
                "order": "created_at.asc",
                "offset": offset,
                "limit": BATCH_SIZE,
            },
            with_count=False,
            use_anon_key=False,
        )
        if not rows:
            break

        for row in rows:
            profile_id_raw = row.get("profile_id")
            releve_id_raw = row.get("id")
            if not profile_id_raw or not releve_id_raw:
                continue

            profile_id = UUID(str(profile_id_raw))
            releve_id = UUID(str(releve_id_raw))
            bank_account_id = row.get("bank_account_id")
            bank_account_uuid = UUID(str(bank_account_id)) if bank_account_id else None
            merchant_entity_raw = row.get("merchant_entity_id")
            if merchant_entity_raw:
                merchant_entity_id = UUID(str(merchant_entity_raw))
            else:
                observed_alias = str(row.get("payee") or row.get("libelle") or "").strip()
                normalized_alias = normalize_merchant_alias(observed_alias) or "inconnu"
                merchant_entity_id = profiles_repository.ensure_merchant_entity_from_alias(
                    profile_id=profile_id,
                    observed_alias=observed_alias or normalized_alias,
                    observed_alias_norm=normalized_alias,
                    merchant_key_norm=normalized_alias,
                )

            decision = decide_releve_classification(
                profile_id=profile_id,
                merchant_entity_id=merchant_entity_id,
                bank_account_id=bank_account_uuid,
                libelle=str(row.get("libelle") or "") or None,
                payee=str(row.get("payee") or "") or None,
                montant=Decimal(str(row.get("montant") or "0")),
                devise=str(row.get("devise") or "CHF"),
                date=date.fromisoformat(str(row.get("date") or "1970-01-01")[:10]),
                metadata=row.get("metadonnees") if isinstance(row.get("metadonnees"), dict) else None,
                repositories=profiles_repository,
            )

            category_id = decision.category_id or import_service._resolve_default_category_id(profile_id=profile_id)

            client.patch_rows(
                table="releves_bancaires",
                query={"id": f"eq.{releve_id}"},
                payload={"category_id": str(category_id), "categorie": None},
                use_anon_key=False,
            )
            updated_count += 1

        offset += len(rows)

    print(f"Backfill terminé: {updated_count} transactions mises à jour.")


if __name__ == "__main__":
    run()
