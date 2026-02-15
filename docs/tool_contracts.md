# Tool Contracts

## Convention de nommage
Format: `finance.<resource>.<action>`
- `finance.transactions.search`
- `finance.accounts.list`
- `finance.categories.list`

## Inputs/Outputs
- Inputs = modèles Pydantic (`shared.models`) sérialisables JSON.
- Outputs = modèles Pydantic ou payload structuré avec pagination.
- Erreurs = `ToolError` normalisé.

### Exemple: finance.transactions.search
**Input**: `TransactionFilters`
- `date_range`, `account_ids`, `category_ids`
- `min_amount`, `max_amount`, `query`
- `page`, `page_size`

**Output**:
```json
{
  "items": ["Transaction"],
  "page": 1,
  "page_size": 50,
  "total": 0
}
```

## Erreurs normalisées
`ToolError.code` doit être l'un de:
- `VALIDATION_ERROR`
- `NOT_FOUND`
- `CONFLICT`
- `UNAUTHORIZED`
- `FORBIDDEN`
- `RATE_LIMITED`
- `BACKEND_UNAVAILABLE`
- `INTERNAL_ERROR`

## Pagination et filtres
- Pagination 1-indexée (`page>=1`).
- `page_size` borné (ex: max 500).
- Tous les filtres sont optionnels et composables.
- Les dates sont ISO-8601; montants en décimal (pas float binaire).
