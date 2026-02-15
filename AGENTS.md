# AGENTS.md — Assistant financier IA

## Objectif
Construire un assistant financier IA orienté **agent + tools** avec séparation stricte des responsabilités:
- `backend/` = logique métier + seul accès Supabase
- `agent/` = orchestration IA uniquement
- `ui/` = interface utilisateur uniquement
- `shared/` = contrats/types/erreurs communs

## Règles non négociables
1. **Source de vérité métier**: réutiliser le code existant de `https://github.com/tristanpfefferle/gestion_financiere.git` via wrappers/adaptateurs backend.
2. **Interdictions absolues**:
   - pas d'accès DB/Supabase depuis `agent/` ou `ui/`
   - pas de logique métier financière dans `agent/` ou `ui/`
   - pas de duplication de logique backend existante
3. Toute opération financière est exposée comme **tool backend** avec contrats typés.
4. Utiliser **Pydantic v2** pour les modèles partagés.
5. Favoriser code modulaire, lisible, typé, testable, production-ready.

## Conventions
- Python: typage explicite, docstrings courtes, interfaces simples avant implémentation.
- Dossiers:
  - `backend/db`: clients et accès datasource
  - `backend/repositories`: abstractions de persistance
  - `backend/services`: services/tools backend
  - `agent`: boucle agent + routeur tools + client backend
  - `ai`: prompts/configs/evals
  - `infra`: migrations, RLS, CI/CD, docker
- Erreurs tools normalisées avec `shared.models.ToolError`.

## Flux applicatif
`User -> UI -> Agent -> Backend -> Supabase -> Backend -> Agent -> UI`

## Commandes dev/run/test
- Installer Python: `python -m pip install -e .[dev]`
- Tests Python: `pytest`
- UI install: `cd ui && npm install`
- UI dev: `cd ui && npm run dev`
- UI build: `cd ui && npm run build`
- CI local rapide: `pytest && (cd ui && npm ci && npm run build)`
