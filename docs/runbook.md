# Runbook (dev)

## Prérequis
- Python 3.10+
- Node.js 20+
- npm 10+

## Variables d'environnement (exemple)
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `OPENAI_API_KEY`

## Lancer en développement
### Python
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest
```

### UI
```bash
cd ui
npm install
npm run dev
```

## Vérifications minimales
- `pytest` valide les imports des couches `shared`, `backend`, `agent`.
- `npm run build` valide le bundle UI minimal.

## Décision d'intégration agent -> backend
Par défaut en monorepo, `agent/clients/backend_client.py` appelle `backend.main.create_backend_services()` en in-process.
Une implémentation HTTP pourra remplacer ce client sans changer le routeur d'outils.
