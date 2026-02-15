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

- `SUPABASE_URL` (backend)
- `SUPABASE_SERVICE_ROLE_KEY` (backend)
- `OPENAI_API_KEY` (agent)
- `APP_ENV` (`dev`, `test`, `prod`)

## Commandes utiles

- Tests Python: `pytest`
- API agent (dev): `uvicorn agent.api:app --reload --port 8000`
- Build UI: `cd ui && npm run build`
- CI locale: `pytest && (cd ui && npm ci && npm run build)`

## Note intégration métier

La logique métier doit venir du repo `gestion_financiere` (wrappers backend). Aucun portage de logique dans `agent/` ou `ui/`.
