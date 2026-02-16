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
- `SUPABASE_ANON_KEY` (backend auth verification)
- `SUPABASE_SERVICE_ROLE_KEY` (backend server-only)
- `AGENT_LLM_ENABLED` (`1`/`true` pour activer le planner LLM, désactivé par défaut)
- `AGENT_LLM_MODEL` (optionnel, recommandé: `gpt-4.1-mini` en prod Render)
- `AGENT_LLM_STRICT` (`1`/`true` pour activer le mode strict de clarification)
- `OPENAI_API_KEY` (agent, requis seulement si `AGENT_LLM_ENABLED` est actif)
- Si votre compte OpenAI ne donne pas accès à `gpt-5`, définir explicitement `AGENT_LLM_MODEL=gpt-4.1-mini`.
- `CORS_ALLOW_ORIGINS` (liste séparée par virgules, ex. `https://ui.onrender.com,https://preview.example.com`)

### UI (`ui/.env`)

- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`
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
  - `SUPABASE_ANON_KEY`
  - `SUPABASE_SERVICE_ROLE_KEY` (server-only)
  - `AGENT_LLM_ENABLED`
  - `AGENT_LLM_MODEL`
  - `AGENT_LLM_STRICT`
  - `OPENAI_API_KEY` (si LLM activé)
  - `CORS_ALLOW_ORIGINS=https://ia-financial-assistant-ui.onrender.com`

#### CORS en production (Render)

L'API charge les origines CORS via `shared.config.cors_allow_origins()`.
Si `CORS_ALLOW_ORIGINS` n'est pas défini en production, cette fonction renvoie `[]` et les appels navigateur depuis la UI Render seront bloqués par CORS.

### UI Vite

- Runtime: Static Site
- Root Directory: `ui`
- Build command: `npm ci && npm run build`
- Publish directory: `dist`
- Variables à définir:
  - `VITE_SUPABASE_URL`
  - `VITE_SUPABASE_ANON_KEY`
  - `VITE_API_URL=https://ia-financial-assistant.onrender.com` (sans slash final)

## Dépannage

- **Erreur**: `RuntimeError: The starlette.testclient module requires the httpx package to be installed.`
  - **Cause**: dépendances dev/tests non installées.
  - **Solution**: exécuter `python -m pip install -e .[dev]` puis relancer `pytest`.

## Note intégration métier

La logique métier doit venir du repo `gestion_financiere` (wrappers backend). Aucun portage de logique dans `agent/` ou `ui/`.

## API Supabase (`releves_bancaires`)

### Variables d'environnement requises

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`

### Outils backend disponibles

- `finance_releves_search`: liste paginée sur `releves_bancaires`
- `finance_releves_sum`: agrégats (`total`, `count`, `average`) avec `Decimal`

Filtres supportés:

- `profile_id` est injecté côté API depuis le token Bearer (jamais fourni par la UI)
- `date_range.start_date` / `date_range.end_date` (optionnel)
- `categorie` (optionnel)
- `merchant_id` (prioritaire sur `merchant`)
- `merchant` (`payee ILIKE %merchant%`)
- `direction` (`ALL`, `DEBIT_ONLY`, `CREDIT_ONLY`)
- `limit`, `offset`

### Exemples de test via `tool_router` (sans dépendre du planner)

```bash
python - <<'PY'
from agent.factory import build_agent_loop

agent_loop = build_agent_loop()

print(agent_loop.tool_router.call("finance_releves_search", {
    "limit": 5,
    "offset": 0,
}, profile_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa").model_dump(mode="json"))

print(agent_loop.tool_router.call("finance_releves_sum", {
    "direction": "DEBIT_ONLY",
    "limit": 50,
    "offset": 0,
}, profile_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa").model_dump(mode="json"))
PY
```

## Auth ↔ profils (liaison utilisateur)

- Le lien canonique entre Supabase Auth et les profils applicatifs est `public.profils.account_id = auth.users.id`.
- Le backend API résout d'abord le `profile_id` via `account_id` (UID Supabase Auth), puis utilise `email` uniquement en fallback.
- L'email ne doit pas être considéré comme identifiant principal de liaison (peut diverger selon les environnements).
