from __future__ import annotations

from backend.services.classification.recurrence import detect_monthly_recurring_clusters


def test_detects_monthly_rent_12_months() -> None:
    transactions = [
        {
            "id": f"tx-{idx}",
            "date": f"2024-{idx:02d}-01",
            "montant": "-1200.00",
            "libelle": "Loyer Appartement",
            "payee": "Régie ABC",
        }
        for idx in range(1, 13)
    ]

    clusters = detect_monthly_recurring_clusters(transactions)

    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.sign == "expense"
    assert cluster.amount_chf == 1200
    assert cluster.stats["count"] == 12
    assert len(cluster.transaction_ids) == 12


def test_ignores_two_occurrences() -> None:
    transactions = [
        {
            "id": "tx-1",
            "date": "2024-01-05",
            "montant": "-19.90",
            "libelle": "Spotify",
            "payee": "Spotify",
        },
        {
            "id": "tx-2",
            "date": "2024-02-05",
            "montant": "-19.90",
            "libelle": "Spotify",
            "payee": "Spotify",
        },
    ]

    clusters = detect_monthly_recurring_clusters(transactions)

    assert clusters == []


def test_detects_with_small_date_drift() -> None:
    transactions = [
        {"id": "tx-1", "date": "2024-01-30", "montant": "-89.90", "libelle": "Assurance", "payee": "Assureur"},
        {"id": "tx-2", "date": "2024-02-28", "montant": "-89.90", "libelle": "Assurance", "payee": "Assureur"},
        {"id": "tx-3", "date": "2024-03-31", "montant": "-89.90", "libelle": "Assurance", "payee": "Assureur"},
        {"id": "tx-4", "date": "2024-04-29", "montant": "-89.90", "libelle": "Assurance", "payee": "Assureur"},
    ]

    clusters = detect_monthly_recurring_clusters(transactions)

    assert len(clusters) == 1
    assert clusters[0].stats["count"] == 4


def test_ignores_non_monthly_pattern() -> None:
    transactions = [
        {"id": "tx-1", "date": "2024-01-01", "montant": "-50", "libelle": "Service X", "payee": "Fournisseur"},
        {"id": "tx-2", "date": "2024-01-10", "montant": "-50", "libelle": "Service X", "payee": "Fournisseur"},
        {"id": "tx-3", "date": "2024-03-15", "montant": "-50", "libelle": "Service X", "payee": "Fournisseur"},
        {"id": "tx-4", "date": "2024-06-20", "montant": "-50", "libelle": "Service X", "payee": "Fournisseur"},
    ]

    clusters = detect_monthly_recurring_clusters(transactions)

    assert clusters == []
