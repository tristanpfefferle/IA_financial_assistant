drop index if exists merchant_suggestions_map_alias_pending_failed_unique_idx;

create unique index if not exists merchant_suggestions_map_alias_unique_idx
    on merchant_suggestions (profile_id, action, observed_alias_norm);

-- Deduplicate merchant_aliases before enforcing uniqueness on alias_norm.
-- Keep one canonical row per alias_norm: oldest created_at first, then smallest id.
with ranked_aliases as (
    select
        id,
        row_number() over (
            partition by alias_norm
            order by created_at asc nulls last, id asc
        ) as rn
    from merchant_aliases
    where alias_norm is not null
)
delete from merchant_aliases ma
using ranked_aliases ra
where ma.id = ra.id
  and ra.rn > 1;

create unique index if not exists merchant_aliases_alias_norm_uidx
    on merchant_aliases (alias_norm);
