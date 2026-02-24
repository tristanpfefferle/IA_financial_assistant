alter table if exists public.account_links
    add column if not exists link_type text not null default 'internal',
    add column if not exists other_party_label text null,
    add column if not exists other_party_email text null;

alter table if exists public.account_links
    drop constraint if exists account_links_link_type_check;

alter table if exists public.account_links
    add constraint account_links_link_type_check
    check (link_type in ('internal', 'external'));

alter table if exists public.account_links
    alter column other_profile_id drop not null;

alter table if exists public.account_links
    drop constraint if exists account_links_link_type_profile_consistency_check;

alter table if exists public.account_links
    add constraint account_links_link_type_profile_consistency_check
    check (
        (link_type = 'internal' and other_profile_id is not null)
        or (link_type = 'external' and other_profile_id is null)
    );

alter table if exists public.shared_expenses
    alter column to_profile_id drop not null;

alter table if exists public.shared_expenses
    add column if not exists other_party_label text null;

alter table if exists public.shared_expense_suggestions
    alter column suggested_to_profile_id drop not null;

alter table if exists public.shared_expense_suggestions
    add column if not exists other_party_label text null;

alter table if exists public.shared_expense_suggestions
    drop constraint if exists shared_expense_suggestions_suggestion_target_check;

alter table if exists public.shared_expense_suggestions
    add constraint shared_expense_suggestions_suggestion_target_check
    check (suggested_to_profile_id is not null or other_party_label is not null);

