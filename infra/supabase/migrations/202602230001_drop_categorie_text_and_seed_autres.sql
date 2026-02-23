-- Ensure category_id is the single source of truth on releves imports.
alter table if exists public.releves_bancaires
    drop column if exists categorie_text;

insert into public.profile_categories (
    profile_id,
    scope,
    is_system,
    system_key,
    name,
    name_norm,
    keywords
)
select
    p.id,
    'personal',
    true,
    'other',
    'Autres',
    'autres',
    '[]'::jsonb
from public.profiles p
where not exists (
    select 1
    from public.profile_categories c
    where c.profile_id = p.id
      and c.scope = 'personal'
      and (c.system_key = 'other' or c.name_norm = 'autres')
);
