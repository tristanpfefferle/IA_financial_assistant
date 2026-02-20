create table if not exists merchant_suggestions (
    id uuid primary key default gen_random_uuid(),
    profile_id uuid not null,
    created_at timestamptz not null default now(),
    status text not null default 'pending',
    action text not null,
    source_merchant_id uuid,
    target_merchant_id uuid,
    suggested_name text,
    suggested_name_norm text,
    suggested_category text,
    confidence numeric,
    rationale text,
    error text,
    sample_aliases jsonb,
    llm_model text,
    llm_run_id text,
    constraint merchant_suggestions_status_check check (status in ('pending', 'applied', 'dismissed', 'failed')),
    constraint merchant_suggestions_action_check check (action in ('rename', 'merge', 'categorize', 'keep')),
    constraint merchant_suggestions_confidence_check check (confidence is null or (confidence >= 0 and confidence <= 1))
);

create index if not exists merchant_suggestions_profile_status_idx
    on merchant_suggestions (profile_id, status);

create index if not exists merchant_suggestions_profile_action_idx
    on merchant_suggestions (profile_id, action);

create index if not exists merchant_suggestions_profile_created_at_idx
    on merchant_suggestions (profile_id, created_at desc);
