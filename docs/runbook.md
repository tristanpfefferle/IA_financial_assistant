# Runbook

## Setup développement

1. Python
   - `python -m venv .venv`
   - `source .venv/bin/activate`
   - `python -m pip install -U pip`
   - `python -m pip install -e .[dev]`
2. UI
   - `cd ui && npm install`

## Variables d'environnement (minimum)

En local/dev, créez un fichier `.env` à partir de `.env.example`.

- `SUPABASE_URL` (backend)
- `SUPABASE_SERVICE_ROLE_KEY` (backend)
- `AGENT_LLM_ENABLED` (`1`/`true` pour activer le planner LLM, désactivé par défaut)
- `AGENT_LLM_MODEL` (optionnel, défaut: `gpt-5`)
- `OPENAI_API_KEY` (agent, requis seulement si `AGENT_LLM_ENABLED` est actif)
- `APP_ENV` (`dev`, `test`, `prod`)

## Commandes utiles

- Tests Python: `pytest`
- API agent (dev): `uvicorn agent.api:app --reload --port 8000`
- Build UI: `cd ui && npm run build`
- CI locale: `pytest && (cd ui && npm ci && npm run build)`

## Dépannage

- **Erreur**: `RuntimeError: The starlette.testclient module requires the httpx package to be installed.`
  - **Cause**: dépendances dev/tests non installées.
  - **Solution**: exécuter `python -m pip install -e .[dev]` puis relancer `pytest`.

## Note intégration métier

La logique métier doit venir du repo `gestion_financiere` (wrappers backend). Aucun portage de logique dans `agent/` ou `ui/`.
