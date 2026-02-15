# Tool contracts

## Naming

- Format obligatoire: `finance_<resource>_<action>`
- Exemples: `finance_transactions_search`, `finance.accounts.list`
- Les noms invalides (ex: `finance..search`) sont interdits.

## Contrats I/O (Pydantic)

- Inputs et outputs des tools backend sont des modèles Pydantic v2 dans `shared/models.py`.
- Les tools backend lèvent/retournent `ToolError` en cas d'échec fonctionnel.

### `finance_transactions_search`

- **Input**: `TransactionFilters`
  - `account_id: str | None`
  - `category_id: str | None`
  - `date_range: DateRange | None`
  - `min_amount: Decimal | None`
  - `max_amount: Decimal | None`
  - `search: str | None`
  - `limit: int = 50`
  - `offset: int = 0`
- **Output success**: `TransactionSearchResult`
  - `items: list[Transaction]`
  - `limit: int`
  - `offset: int`
  - `total: int | None` (peut rester `None` si le backend ne calcule pas le total)
- **Output error**: `ToolError`


### `finance_transactions_sum`

- **Input**: `TransactionFilters`
  - mêmes filtres que `finance_transactions_search`
  - `direction: TransactionSumDirection | None`
    - `ALL` ou `None`: somme de tous les montants
    - `DEBIT_ONLY`: somme uniquement des montants négatifs
    - `CREDIT_ONLY`: somme uniquement des montants positifs
- **Output success**: `TransactionSumResult`
  - `total: Money`
  - `count: int`
  - `limit: int`
  - `offset: int`
  - `filters: TransactionFilters | None`
- **Output error**: `ToolError`

Exemples payload JSON:

```json
{
  "search": "coffee",
  "date_range": {"start_date": "2025-01-01", "end_date": "2025-01-31"},
  "direction": "DEBIT_ONLY"
}
```

```json
{
  "account_id": "acc_main",
  "min_amount": "-100.00",
  "max_amount": "0",
  "direction": "ALL",
  "limit": 50,
  "offset": 0
}
```

## Erreur standard

`ToolError` inclut:
- `code`: identifiant stable de type `ToolErrorCode`
  - `VALIDATION_ERROR`
  - `UNKNOWN_TOOL`
  - `BACKEND_ERROR`
  - `NOT_FOUND`
- `message`: description lisible
- `details`: map optionnelle d'informations techniques

## Pagination et filtres

- Pagination implémentée: `limit` + `offset`.
- `finance_transactions_search` renvoie toujours un objet paginé stable (`TransactionSearchResult`) même quand `items` est vide.
- Filtres actuels appliqués dans le repository mock dev/test:
  - `account_id`
  - `search` (substring case-insensitive sur `description`)
  - `date_range` (comparaison sur `booked_at.date()`)
  - `min_amount` / `max_amount` (comparaison sur `Money.amount` en `Decimal`)
