alter table if exists merchant_suggestions
    add column if not exists times_seen integer not null default 1,
    add column if not exists last_seen timestamptz not null default now(),
    add column if not exists updated_at timestamptz not null default now();

with ranked as (
    select
        id,
        profile_id,
        observed_alias_norm,
        row_number() over (
            partition by profile_id, observed_alias_norm
            order by created_at asc nulls last, id asc
        ) as rn,
        count(*) over (partition by profile_id, observed_alias_norm) as group_count,
        max(coalesce(last_seen, created_at, now())) over (partition by profile_id, observed_alias_norm) as max_last_seen
    from merchant_suggestions
    where observed_alias_norm is not null
),
updated_kept as (
    update merchant_suggestions ms
    set
        times_seen = greatest(coalesce(ms.times_seen, 1), ranked.group_count),
        last_seen = ranked.max_last_seen,
        updated_at = now()
    from ranked
    where ms.id = ranked.id
      and ranked.rn = 1
    returning ms.id
)
delete from merchant_suggestions ms
using ranked
where ms.id = ranked.id
  and ranked.rn > 1;

drop index if exists merchant_suggestions_map_alias_unique_idx;
drop index if exists merchant_suggestions_map_alias_pending_failed_unique_idx;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'merchant_suggestions_profile_observed_alias_norm_key'
    ) then
        alter table merchant_suggestions
            add constraint merchant_suggestions_profile_observed_alias_norm_key
            unique (profile_id, observed_alias_norm);
    end if;
end $$;

create or replace function merchant_suggestions_touch_map_alias()
returns trigger
language plpgsql
as $$
begin
    if new.action = 'map_alias' then
        new.times_seen := coalesce(old.times_seen, 0) + 1;
        new.last_seen := now();
        new.updated_at := now();
    end if;
    return new;
end;
$$;

drop trigger if exists trg_merchant_suggestions_touch_map_alias on merchant_suggestions;

create trigger trg_merchant_suggestions_touch_map_alias
before update on merchant_suggestions
for each row
when (new.action = 'map_alias')
execute function merchant_suggestions_touch_map_alias();
