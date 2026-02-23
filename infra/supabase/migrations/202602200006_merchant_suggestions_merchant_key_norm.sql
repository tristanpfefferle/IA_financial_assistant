create extension if not exists unaccent;

create or replace function derive_merchant_key_norm(input_text text)
returns text
language plpgsql
as $$
declare
    normalized text;
    working text;
    marker text;
    marker_pos integer;
    cut_pos integer;
    tokens text[];
begin
    normalized := regexp_replace(unaccent(lower(coalesce(input_text, ''))), '\s+', ' ', 'g');
    normalized := btrim(normalized);

    if normalized = '' then
        return null;
    end if;

    working := normalized;
    cut_pos := length(working) + 1;

    foreach marker in array array[';', ' - ', ' paiement ', ' debit ', ' credit ', 'ordre e-banking', 'motif du paiement']
    loop
        marker_pos := strpos(working, marker);
        if marker_pos > 0 and marker_pos < cut_pos then
            cut_pos := marker_pos;
        end if;
    end loop;

    working := btrim(substr(working, 1, cut_pos - 1), ' -;:');

    working := regexp_replace(working, '\b(?:qrr|iban|twint-?acc)\b[[:alnum:]\s-]*', ' ', 'g');
    working := regexp_replace(working, '\b(?:no|numero|num|n)\s*(?:de\s*)?(?:transaction|reference|ref)\b[[:alnum:]\s-]*', ' ', 'gi');
    working := regexp_replace(working, '\+\d{8,15}\b', ' ', 'g');
    working := regexp_replace(working, '\b\d{2}[./-]\d{2}[./-]\d{2,4}\b', ' ', 'g');
    working := regexp_replace(working, '\b\d{4}\b\s+[[:alpha:]]{3,}$', ' ', 'g');
    working := regexp_replace(working, '[- ]\d{3,6}\b.*$', '', 'g');
    working := regexp_replace(working, '\s+', ' ', 'g');
    working := btrim(working);

    if working = '' then
        return null;
    end if;

    working := btrim(working, ' -;:');
    tokens := regexp_split_to_array(working, '\s+');
    if array_length(tokens, 1) = 2
       and tokens[1] in ('coop', 'migros', 'aldi', 'lidl', 'denner', 'manor')
       and tokens[2] not in ('city', 'market', 'pronto', 'express', 'shop', 'store') then
        working := tokens[1];
    end if;

    return nullif(working, '');
end;
$$;

alter table if exists merchant_suggestions
    add column if not exists merchant_key_norm text;

update merchant_suggestions
set merchant_key_norm = derive_merchant_key_norm(coalesce(observed_alias, observed_alias_norm))
where merchant_key_norm is null;

with ranked as (
    select
        id,
        profile_id,
        merchant_key_norm,
        row_number() over (
            partition by profile_id, merchant_key_norm
            order by created_at asc nulls last, id asc
        ) as rn,
        count(*) over (partition by profile_id, merchant_key_norm) as group_count,
        max(coalesce(last_seen, created_at, now())) over (partition by profile_id, merchant_key_norm) as max_last_seen,
        first_value(observed_alias) over (
            partition by profile_id, merchant_key_norm
            order by coalesce(last_seen, created_at, now()) desc, created_at desc nulls last, id desc
        ) as latest_observed_alias,
        first_value(observed_alias_norm) over (
            partition by profile_id, merchant_key_norm
            order by coalesce(last_seen, created_at, now()) desc, created_at desc nulls last, id desc
        ) as latest_observed_alias_norm
    from merchant_suggestions
    where merchant_key_norm is not null
),
updated_kept as (
    update merchant_suggestions ms
    set
        observed_alias = ranked.latest_observed_alias,
        observed_alias_norm = ranked.latest_observed_alias_norm,
        times_seen = ranked.group_count,
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

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'merchant_suggestions_profile_merchant_key_norm_key'
    ) then
        alter table merchant_suggestions
            add constraint merchant_suggestions_profile_merchant_key_norm_key
            unique (profile_id, merchant_key_norm);
    end if;
end $$;
