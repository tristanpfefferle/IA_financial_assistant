# backend

Couche métier unique avec accès Supabase. Cette couche intégrera explicitement les fonctions du repo `gestion_financiere` (source de vérité) via wrappers/adaptateurs.

> Note: dans ce projet, les outils "transactions" sont une vue logique de `public.releves_bancaires` (source de vérité métier).
