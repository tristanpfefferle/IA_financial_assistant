create table if not exists public.profile_share_rules (
    id uuid primary key default gen_random_uuid(),
    profile_id uuid not null references public.profils(id) on delete cascade,
    rule_type text not null,
    rule_key text not null,
    action text not null,
    boost_value numeric null,
    created_at timestamptz not null default now(),
    unique (profile_id, rule_type, rule_key),
    constraint profile_share_rules_action_check check (action in ('force_share', 'force_exclude', 'boost')),
    constraint profile_share_rules_boost_check check (
        (action = 'boost' and boost_value is not null)
        or (action <> 'boost' and boost_value is null)
    )
);

create index if not exists idx_profile_share_rules_profile_id
    on public.profile_share_rules (profile_id);
