from decimal import Decimal

from backend.reporting.spending_report import SpendingReportData, _build_cashflow_summary_table


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
