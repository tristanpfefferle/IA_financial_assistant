-- Deduplicate merchant suggestions before enforcing uniqueness on
-- (profile_id, action, observed_alias_norm).
with ranked as (
    select
        id,
        profile_id,
        action,
        observed_alias_norm,
        row_number() over (
            partition by profile_id, action, observed_alias_norm
            order by
                coalesce(updated_at, created_at) desc nulls last,
                created_at desc nulls last,
                id desc
        ) as rn,
        sum(coalesce(times_seen, 1)) over (
            partition by profile_id, action, observed_alias_norm
        ) as sum_times_seen,
        max(last_seen) over (
            partition by profile_id, action, observed_alias_norm
        ) as max_last_seen
    from merchant_suggestions
    where observed_alias_norm is not null
      and btrim(observed_alias_norm) <> ''
),
keepers as (
    select
        id,
        greatest(1, sum_times_seen) as merged_times_seen,
        max_last_seen
    from ranked
    where rn = 1
)
update merchant_suggestions ms
set
    times_seen = keepers.merged_times_seen,
    last_seen = coalesce(keepers.max_last_seen, ms.last_seen)
from keepers
where ms.id = keepers.id;

with duplicates as (
    select id
    from (
        select
            id,
            row_number() over (
                partition by profile_id, action, observed_alias_norm
                order by
                    coalesce(updated_at, created_at) desc nulls last,
                    created_at desc nulls last,
                    id desc
            ) as rn
        from merchant_suggestions
        where observed_alias_norm is not null
          and btrim(observed_alias_norm) <> ''
    ) ranked
    where rn > 1
)
delete from merchant_suggestions ms
using duplicates d
where ms.id = d.id;

create unique index if not exists merchant_suggestions_profile_action_observed_alias_norm_uidx
    on merchant_suggestions (profile_id, action, observed_alias_norm);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'merchant_suggestions_profile_action_observed_alias_norm_key'
    ) then
        alter table merchant_suggestions
            add constraint merchant_suggestions_profile_action_observed_alias_norm_key
            unique using index merchant_suggestions_profile_action_observed_alias_norm_uidx;
    end if;
end
$$;
