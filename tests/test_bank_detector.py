from backend.services.releves_import.bank_detector import detect_bank_from_csv_bytes


def test_detect_bank_from_csv_bytes_revolut() -> None:
    content = b"Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance\nCARD,CURRENT,2025-01-01,2025-01-01,Coffee,-4.20,0.00,CHF,COMPLETED,100.00\n"
    assert detect_bank_from_csv_bytes(content) == "revolut"


def test_detect_bank_from_csv_bytes_ubs() -> None:
    content = b"Booking date;Value date;Transaction details;Debit;Credit\n2025-01-01;2025-01-02;Paiement carte;12.50;\n"
    assert detect_bank_from_csv_bytes(content) == "ubs"


def test_detect_bank_from_csv_bytes_raiffeisen() -> None:
    content = b"Buchungsdatum;Valutadatum;Mitteilung;Belastung;Gutschrift\n01.01.2025;02.01.2025;Kartenzahlung;25.00;\n"
    assert detect_bank_from_csv_bytes(content) == "raiffeisen"


def test_detect_bank_from_csv_bytes_unknown_returns_none() -> None:
    content = b"date;label;amount\n2025-01-01;x;-10.00\n"
    assert detect_bank_from_csv_bytes(content) is None


def test_detect_bank_from_csv_bytes_without_separator_returns_none() -> None:
    content = b"Booking date Value date Transaction details Debit Credit\n2025-01-01 2025-01-02 Paiement 12.50\n"
    assert detect_bank_from_csv_bytes(content) is None
