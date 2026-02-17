# Mapping ancien → nouveau (import multi-banques)

- `core/detection_source.py` → `backend/services/releves_import/source_detection.py`
  - `detecter_source_fichier` → `detect_source`
- `core/routeur_comptes.py` → `backend/services/releves_import/routing.py`
  - `routeur_comptes` → `route_bank_parser`
- `core/extraction_ubs.py` → `backend/services/releves_import/parsers/ubs.py`
  - `parse_ubs` → `parse_ubs_csv`
  - `lire_csv_contenu` → `parse_generic_csv` (dans `parsers/generic_csv.py`)
- `core/extraction_raiffeisen.py` → `backend/services/releves_import/parsers/raiffeisen.py`
  - `parse_raiffeisen` → `parse_raiffeisen_csv`
- `core/supabase.py` → `backend/services/releves_import/importer.py` + `backend/services/releves_import/dedup.py`
  - `inserer_transactions` → `import_releves` (`RelevesImportService.import_releves`) + `compare_rows`
