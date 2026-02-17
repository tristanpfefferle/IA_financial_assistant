create unique index if not exists bank_accounts_profile_id_name_lower_unique
on public.bank_accounts (profile_id, lower(name));
