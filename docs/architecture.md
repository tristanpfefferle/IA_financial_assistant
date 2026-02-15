# Architecture de production — Assistant financier IA

## Diagramme textuel

```text
User
  -> UI (React/Vite)
    -> Agent (orchestration IA + tool routing)
      -> Backend (services métier + repositories)
        -> Supabase (PostgreSQL + RLS)
      <- données structurées / ToolError
    <- réponse naturelle
  <- rendu chat
```

## Responsabilités
- **ui/**: interface chat minimale, debug, appels API agent.
- **agent/**: boucle agentique, tool calling, orchestration conversation.
- **backend/**: logique métier + accès DB Supabase (unique couche autorisée).
- **shared/**: contrats (Pydantic), types, erreurs normalisées.
- **ai/**: prompts, configs, evals.
- **infra/**: migrations Supabase, seed, RLS, docker, CI.
- **docs/**: architecture, runbook, contrats tools.

## Frontières strictes
1. `agent/` et `ui/` n'ont aucun accès direct DB.
2. `agent/` n'implémente pas de logique métier financière.
3. Toute logique financière est portée par `backend/`, en réutilisant le repo de référence.
4. `shared/` définit les schémas échangés entre couches.

## Intégration backend existant
Source de vérité métier:
`https://github.com/tristanpfefferle/gestion_financiere.git`

Stratégie:
- encapsuler les fonctions existantes dans `backend/services/`;
- exposer des méthodes stables à `agent/` via client local/API;
- éviter toute duplication de logique.
