# Runbook

## Setup développement

1. Python
   - `python -m venv .venv`
   - `source .venv/bin/activate`
   - `python -m pip install -U pip`
   - `python -m pip install -e .[dev]`
2. UI
   - `cd ui && npm install`

## Variables d'environnement

En local/dev, créez un fichier `.env` à partir de `.env.example`.

En production (`APP_ENV=prod`), le chargement dotenv n'est pas requis : Render injecte les variables d'environnement directement.

### API (racine du projet)

- `APP_ENV` (`dev`, `local`, `test`, `ci`, `prod`)
- `SUPABASE_URL` (backend)
- `SUPABASE_SERVICE_ROLE_KEY` (backend)
- `AGENT_LLM_ENABLED` (`1`/`true` pour activer le planner LLM, désactivé par défaut)
- `AGENT_LLM_MODEL` (optionnel, défaut: `gpt-5`)
- `AGENT_LLM_STRICT` (`1`/`true` pour activer le mode strict de clarification)
- `OPENAI_API_KEY` (agent, requis seulement si `AGENT_LLM_ENABLED` est actif)
- `CORS_ALLOW_ORIGINS` (liste séparée par virgules, ex. `https://ui.onrender.com,https://preview.example.com`)

### UI (`ui/.env`)

- `VITE_API_URL` (URL publique de l'API, ex. `http://127.0.0.1:8000` en local)

## Commandes utiles

- Tests Python: `pytest`
- API agent (dev): `uvicorn agent.api:app --reload --port 8000`
- UI dev: `cd ui && npm run dev`
- Build UI: `cd ui && npm run build`
- CI locale: `pytest && (cd ui && npm ci && npm run build)`

## Déploiement Render

### API FastAPI

- Runtime: Python
- Start command: `uvicorn agent.api:app --host 0.0.0.0 --port $PORT`
- Variables à définir:
  - `APP_ENV=prod`
  - `SUPABASE_URL`
  - `SUPABASE_SERVICE_ROLE_KEY`
  - `AGENT_LLM_ENABLED`
  - `AGENT_LLM_MODEL`
  - `AGENT_LLM_STRICT`
  - `OPENAI_API_KEY` (si LLM activé)
  - `CORS_ALLOW_ORIGINS=https://<frontend-render-domain>`

### UI Vite

- Runtime: Static Site
- Root Directory: `ui`
- Build command: `npm ci && npm run build`
- Publish directory: `dist`
- Variable à définir:
  - `VITE_API_URL=https://<api-render-domain>`

## Dépannage

- **Erreur**: `RuntimeError: The starlette.testclient module requires the httpx package to be installed.`
  - **Cause**: dépendances dev/tests non installées.
  - **Solution**: exécuter `python -m pip install -e .[dev]` puis relancer `pytest`.

## Note intégration métier

La logique métier doit venir du repo `gestion_financiere` (wrappers backend). Aucun portage de logique dans `agent/` ou `ui/`.
