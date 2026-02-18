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


## Guardian LLM (validation ciblée des plans déterministes)

- Le déterminisme reste prioritaire: un `ToolCallPlan` à confiance `high` est exécuté sans validation LLM additionnelle.
- Chaque plan outil transporte `meta["confidence"]` (`high`/`medium`/`low`) et `meta["confidence_reasons"]`.
- Règles de confiance (heuristiques agent):
  - `low`: follow-up court dépendant de mémoire/contexte (ex: "et en janvier 2026") ou période injectée implicitement.
  - `medium`: filtre potentiellement inféré (ex: catégorie issue d'un focus ambigu).
  - `high`: intention + période/filtres explicites.
- Si confiance non `high` et LLM activé, `agent/llm_judge.py` agit comme **guardian**:
  - `approve`: on garde le plan déterministe.
  - `repair`: correction proposée, puis contrôlée via allowlist + validation payload existantes avant exécution.
  - `clarify`: question utilisateur (pas d'exécution automatique).
- Si LLM indisponible et confiance `low`, l'agent demande une clarification plutôt que d'exécuter un plan fragile.
