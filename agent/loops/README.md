# Loops conversationnelles

Le moteur `agent/loops` impose **une seule boucle active** à la fois, persistée dans `chat_state.state["loop"]`.

## Concept
- `LoopContext`: état actif (`loop_id`, `step`, `data`, `blocking`).
- `LoopReply`: réponse + transition éventuelle (`next_loop`).
- `route_message`: politique déterministe-first.

## Ajouter une loop
1. Implémenter l'interface `Loop` (`id`, `blocking`, `can_enter`, `handle`).
2. Enregistrer la loop dans `LoopRegistry`.
3. Définir transitions via `LoopReply.next_loop`.

## Blocking vs non-blocking
- `blocking=True`: si hors sujet, le routeur réoriente vers la question courante.
- `blocking=False`: autorise les digressions/switch plus facilement.

## Tests
- Unitaires routeur: vérifier réorientation, switch, fallback LLM.
- API: vérifier la persistance `chat_state.state["loop"]` entre messages.
