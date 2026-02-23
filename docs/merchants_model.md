# Merchants model (canonical + aliases)

## Source of truth
- `merchant_entities` est la source de vérité globale pour les marchands (nom canonique stable, normalisé, réutilisable).
- `merchant_aliases` stocke les alias observés (`alias` / `alias_norm`) et les relie à une entrée canonique de `merchant_entities`.

## File de décisions
- `merchant_suggestions` est la queue de décisions opérables (`action`) pour traiter les cas ambigus:
  - `map_alias` (lier un alias observé à une entité),
  - `categorize` (proposition de catégorie),
  - `merge` (fusion d’entités),
  - `rename` (renommage canonique).
- Les suggestions sont suivies par statut (`pending`, `applied`, `failed`) pour garantir un traitement idempotent et auditable.

## Statut de la table `merchants`
- `merchants` est une table **legacy** encore utilisée pour certains flux historiques/backward-compatibility (notamment des endpoints existants et des données profil héritées).
- La direction cible reste le modèle `merchant_entities` + `merchant_aliases` + `merchant_suggestions`.
- Toute nouvelle canonicalisation doit éviter de dériver directement un `canonical_name` depuis un libellé brut de relevé.
