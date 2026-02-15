# Architecture

## Diagramme (texte)

```text
User
  |
  v
UI (React/Vite/TS)
  | HTTP
  v
Agent (orchestration IA)
  | in-process client (par défaut)
  v
Backend (tools + services + repositories)
  | SQL / Supabase client
  v
Supabase (PostgreSQL + RLS)
```

## Responsabilités

- **ui/**: interface chat, rendu, appels API vers l'agent, aucune logique métier.
- **agent/**: interprète les intentions, choisit des tools backend, compose la réponse utilisateur, aucun accès DB.
- **backend/**: contient la logique métier et l'accès Supabase, expose des tools backend structurés.
- **shared/**: modèles Pydantic communs, contrats et erreurs partagés.
- **ai/**: prompts/configuration/evaluations IA.
- **infra/**: migrations Supabase, RLS, seed, CI/CD, docker.
- **docs/**: décisions d'architecture, contrats de tools, runbook opérationnel.

## Frontières strictes

1. `agent/` et `ui/` n'accèdent jamais à Supabase.
2. Toute logique financière réside dans `backend/`.
3. `backend/` réutilise le repository existant `gestion_financiere` comme source de vérité via wrappers.
4. Les entrées/sorties tools sont typées via `shared.models`.
