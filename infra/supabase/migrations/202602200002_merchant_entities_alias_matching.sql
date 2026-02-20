alter table if exists merchant_suggestions
    add column if not exists observed_alias text,
    add column if not exists observed_alias_norm text,
    add column if not exists suggested_entity_name text;

alter table if exists merchant_suggestions
    drop constraint if exists merchant_suggestions_action_check;

alter table if exists merchant_suggestions
    add constraint merchant_suggestions_action_check
    check (action in ('rename', 'merge', 'categorize', 'keep', 'map_alias'));

create unique index if not exists merchant_suggestions_map_alias_pending_failed_unique_idx
    on merchant_suggestions (profile_id, action, observed_alias_norm)
    where action = 'map_alias' and status in ('pending', 'failed');

create index if not exists merchant_aliases_alias_norm_idx
    on merchant_aliases (alias_norm);

create unique index if not exists merchant_entities_canonical_name_norm_country_uidx
    on merchant_entities (canonical_name_norm, country);
