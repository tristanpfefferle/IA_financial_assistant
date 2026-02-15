# AGENTS.md — Assistant financier IA

## Objectif
Construire un assistant financier IA **production-ready** avec séparation stricte des responsabilités entre `ui/`, `agent/`, `backend/`, `shared/`, `ai/`, `infra/` et `docs/`.

## Règles non négociables
- `backend/` est la **seule** couche autorisée à accéder à Supabase.
- `agent/` orchestre les tools et n'implémente **aucune logique métier**.
- `ui/` affiche et collecte les interactions utilisateur, sans logique métier.
- `shared/` centralise les contrats communs (Pydantic, erreurs, types).
- Réutiliser les fonctions de référence depuis:
  `https://github.com/tristanpfefferle/gestion_financiere.git`.
  Ne pas dupliquer la logique métier existante.

## Conventions
- Python 3.10+, typage explicite, modèles Pydantic.
- Nommage tool: `finance.<resource>.<action>` (ex: `finance.transactions.search`).
- Erreurs normalisées (`ToolError`) et payloads sérialisables.
- Documentation d'architecture et contrats maintenue dans `docs/`.

## Flux cible
`User -> UI -> Agent -> Backend -> Supabase -> Backend -> Agent -> UI`

## Commandes run/test
- Backend/Agent (imports + tests):
  - `pytest`
- UI (build):
  - `cd ui && npm install`
  - `cd ui && npm run build`
- UI (dev):
  - `cd ui && npm run dev`
