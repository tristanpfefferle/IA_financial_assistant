create table if not exists public.import_jobs (
    id uuid primary key default gen_random_uuid(),
    profile_id uuid not null references public.profils(id) on delete cascade,
    status text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    error_message text null,
    total_transactions int null,
    processed_transactions int null,
    total_llm_items int null,
    processed_llm_items int null,
    constraint import_jobs_status_check check (status in ('pending', 'running', 'done', 'error'))
);

create table if not exists public.import_job_events (
    id bigserial primary key,
    job_id uuid not null references public.import_jobs(id) on delete cascade,
    seq int not null,
    kind text not null,
    message text not null,
    progress double precision null,
    payload jsonb null,
    created_at timestamptz not null default now(),
    constraint import_job_events_progress_check check (progress is null or (progress >= 0 and progress <= 1)),
    constraint import_job_events_job_seq_unique unique (job_id, seq)
);

create index if not exists idx_import_job_events_job_id_created_at
    on public.import_job_events (job_id, created_at);

create or replace function import_jobs_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists trg_import_jobs_touch_updated_at on public.import_jobs;

create trigger trg_import_jobs_touch_updated_at
before update on public.import_jobs
for each row
execute function import_jobs_touch_updated_at();
