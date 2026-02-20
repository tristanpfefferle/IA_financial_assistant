drop index if exists merchant_suggestions_map_alias_unique_idx;
drop index if exists merchant_suggestions_map_alias_pending_failed_unique_idx;

create unique index if not exists merchant_suggestions_map_alias_pending_failed_unique_idx
    on merchant_suggestions (profile_id, action, observed_alias_norm)
    where status in ('pending', 'failed');
