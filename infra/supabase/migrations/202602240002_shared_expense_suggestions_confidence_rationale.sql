alter table public.shared_expense_suggestions
add column if not exists confidence numeric,
add column if not exists rationale text;
