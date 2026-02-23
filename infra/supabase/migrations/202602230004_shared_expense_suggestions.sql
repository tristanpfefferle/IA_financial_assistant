create table if not exists public.shared_expense_suggestions (
    id uuid primary key default gen_random_uuid(),
    profile_id uuid not null,
    transaction_id uuid not null,
    link_pair_id uuid null,
    link_id uuid null,
    suggested_to_profile_id uuid not null,
    suggested_split_ratio_other numeric not null default 0.5,
    confidence double precision null,
    rationale text null,
    status text not null default 'pending' check (status in ('pending', 'applied', 'dismissed', 'failed')),
    model text null,
    run_id text null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_shared_exp_sugg_profile_status
    on public.shared_expense_suggestions (profile_id, status, created_at desc);

create unique index if not exists idx_shared_exp_sugg_pending_dedup
    on public.shared_expense_suggestions (
        profile_id,
        transaction_id,
        suggested_to_profile_id,
        suggested_split_ratio_other
    )
    where status = 'pending';
