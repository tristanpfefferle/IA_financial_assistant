from decimal import Decimal

from backend.reporting.spending_report import (
    SpendingReportData,
    _build_cashflow_summary_table,
    _build_shared_expenses_summary_table,
)


def test_build_cashflow_summary_table_includes_net_variation_with_transfers() -> None:
    data = SpendingReportData(
        period_label="2026-01-01 → 2026-01-31",
        start_date="2026-01-01",
        end_date="2026-01-31",
        total=Decimal("100"),
        count=2,
        currency="CHF",
        categories=[],
        transactions=[],
        cashflow_income=Decimal("150"),
        cashflow_expense=Decimal("-80"),
        cashflow_net=Decimal("70"),
        cashflow_internal_transfers=Decimal("20"),
        cashflow_net_including_transfers=Decimal("90"),
        cashflow_currency="CHF",
    )

    table = _build_cashflow_summary_table(data)

    assert table._cellvalues[3][0] == "Variation nette (incl. transferts)"
    assert table._cellvalues[3][1] == "90.00 CHF"


def test_build_shared_expenses_summary_table_includes_effective_spending_block() -> (
    None
):
    data = SpendingReportData(
        period_label="2026-01-01 → 2026-01-31",
        start_date="2026-01-01",
        end_date="2026-01-31",
        total=Decimal("320"),
        count=4,
        currency="CHF",
        categories=[],
        transactions=[],
        effective_total=Decimal("280"),
        shared_outgoing=Decimal("30"),
        shared_incoming=Decimal("10"),
        shared_net_balance=Decimal("-20"),
    )

    table = _build_shared_expenses_summary_table(data)

    assert table._cellvalues[0] == ["Total dépenses", "320.00 CHF"]
    assert table._cellvalues[1] == ["Partage sortant", "30.00 CHF"]
    assert table._cellvalues[2] == ["Partage entrant", "10.00 CHF"]
    assert table._cellvalues[3] == ["Solde partage", "-20.00 CHF"]
    assert table._cellvalues[4] == ["Total effectif", "280.00 CHF"]


def test_build_shared_expenses_summary_table_bolds_only_last_row_without_breaking_base_style() -> (
    None
):
    data = SpendingReportData(
        period_label="2026-01-01 → 2026-01-31",
        start_date="2026-01-01",
        end_date="2026-01-31",
        total=Decimal("320"),
        count=4,
        currency="CHF",
        categories=[],
        transactions=[],
    )

    table = _build_shared_expenses_summary_table(data)

    for row in table._cellStyles[:-1]:
        assert row[0].fontname == "Helvetica"
        assert row[1].fontname == "Helvetica"
        assert row[0].alignment == "LEFT"
        assert row[1].alignment == "RIGHT"
        assert row[0].valign == "MIDDLE"
        assert row[1].valign == "MIDDLE"
        assert row[0].topPadding == 3
        assert row[1].topPadding == 3
        assert row[0].bottomPadding == 3
        assert row[1].bottomPadding == 3

    assert table._cellStyles[-1][0].fontname == "Helvetica-Bold"
    assert table._cellStyles[-1][1].fontname == "Helvetica-Bold"
    assert table._cellStyles[-1][0].alignment == "LEFT"
    assert table._cellStyles[-1][1].alignment == "RIGHT"
    assert table._cellStyles[-1][0].valign == "MIDDLE"
    assert table._cellStyles[-1][1].valign == "MIDDLE"
    assert table._cellStyles[-1][0].topPadding == 3
    assert table._cellStyles[-1][1].topPadding == 3
    assert table._cellStyles[-1][0].bottomPadding == 3
    assert table._cellStyles[-1][1].bottomPadding == 3
