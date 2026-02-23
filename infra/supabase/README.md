# supabase

Contient les migrations SQL, politiques RLS et scripts de seed.

## Exécuter les migrations

- Local (Supabase CLI): `supabase migration up`
- Environnement lié (remote): `supabase db push`

La migration `202602230003_merchant_suggestions_observed_alias_unique.sql`
déduplique `merchant_suggestions` en conservant la ligne la plus récente
(`updated_at`/`created_at`), agrège `times_seen`, puis ajoute la contrainte
UNIQUE `(profile_id, action, observed_alias_norm)`.
