create table if not exists public.transaction_clusters (
    id uuid primary key default gen_random_uuid(),
    profile_id uuid not null references public.profils(id) on delete cascade,
    cluster_type text not null check (cluster_type in ('recurring', 'twint_p2p', 'bank_transfer_unknown')),
    cluster_key text not null,
    stats jsonb not null default '{}'::jsonb,
    status text not null default 'pending' check (status in ('pending', 'applied', 'dismissed', 'failed')),
    suggested_category_id uuid null references public.profile_categories(id),
    confidence double precision null,
    rationale text null,
    model text null,
    run_id text null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint transaction_clusters_profile_type_key_unique unique (profile_id, cluster_type, cluster_key)
);

create index if not exists idx_transaction_clusters_profile_status
    on public.transaction_clusters (profile_id, status);

create table if not exists public.transaction_cluster_items (
    cluster_id uuid not null references public.transaction_clusters(id) on delete cascade,
    transaction_id uuid not null references public.releves_bancaires(id) on delete cascade,
    primary key (cluster_id, transaction_id)
);

create or replace function transaction_clusters_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists trg_transaction_clusters_touch_updated_at on public.transaction_clusters;

create trigger trg_transaction_clusters_touch_updated_at
before update on public.transaction_clusters
for each row
execute function transaction_clusters_touch_updated_at();
