-- Enforce global merchant uniqueness and alias uniqueness for safe concurrent imports.

create unique index if not exists merchant_entities_canonical_name_norm_uq_idx
    on public.merchant_entities (canonical_name_norm);

create unique index if not exists merchant_aliases_alias_norm_uq_idx
    on public.merchant_aliases (alias_norm);

create index if not exists merchant_aliases_alias_norm_idx
    on public.merchant_aliases (alias_norm);

create index if not exists merchant_aliases_merchant_entity_id_idx
    on public.merchant_aliases (merchant_entity_id);
