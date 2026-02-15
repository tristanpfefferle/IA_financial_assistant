# Tool contracts

## Naming

- Format recommandé: `finance.<domaine>.<action>`
- Exemples: `finance.transactions.search`, `finance.accounts.list`

## Contrats I/O (Pydantic)

- Inputs et outputs des tools backend sont des modèles Pydantic v2 dans `shared/models.py`.
- Les tools backend lèvent/retournent `ToolError` en cas d'échec fonctionnel.

## Erreur standard

`ToolError` inclut:
- `code`: identifiant stable (`INVALID_FILTER`, `NOT_FOUND`, etc.)
- `message`: description lisible
- `details`: map optionnelle d'informations techniques

## Pagination et filtres

- Pagination recommandée: `limit` + `offset` (ou cursor ultérieurement).
- Filtres centralisés via `TransactionFilters`:
  - `account_id`, `category_id`, `date_range`, `min_amount`, `max_amount`, `search`
- Les defaults doivent être explicites dans les contrats.
