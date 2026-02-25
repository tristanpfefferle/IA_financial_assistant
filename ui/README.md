# UI — Assistant financier IA

## Configuration

Copier `.env.example` vers `.env` puis renseigner:

- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`
- `VITE_API_URL` (par défaut `http://127.0.0.1:8000`)
- `VITE_UI_DEBUG=true` pour activer le bandeau debug de session

## Lancer l'UI

```bash
npm install
npm run dev
```

## Vérifications manuelles recommandées

- [ ] Login OK avec email/mot de passe Supabase valides.
- [ ] Envoi de message chat OK (la réponse IA s'affiche).
- [ ] Si token expiré, le refresh de session permet l'envoi chat sans 401 utilisateur.
- [ ] Le bouton **Se déconnecter** retourne bien à l'écran de login.
- [ ] En cas d'erreur API (401/500), un message lisible s'affiche avec le détail backend si présent.

## Check-list UI chat plein écran

- [ ] Le `body` ne scrolle plus.
- [ ] La liste de messages scrolle correctement.
- [ ] Le composer reste visible en bas.
- [ ] Le bouton ↓ apparaît quand on remonte et disparaît en bas.

## ActionPanel

Le composant `ActionPanel` (dans `ui/src/chat/ActionPanel.tsx`) garde la zone basse du chat stable (même conteneur) et change uniquement son contenu selon `uiState`:

- `none`: n’affiche aucune action.
- `quick_replies`: affiche uniquement les quick replies (chips/boutons).
- `text`: affiche uniquement le champ texte + bouton d’envoi.
- `quick_replies_text`: affiche les quick replies et le champ texte.

Règle d’implémentation: la structure globale de l’UI ne change pas, seul `uiState` pilote l’affichage des actions en bas du chat.
