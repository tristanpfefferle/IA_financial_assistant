"""Generate spending report PDFs for finance endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@dataclass(slots=True)
class SpendingCategoryRow:
    """Aggregated spending by category."""

    name: str
    amount: Decimal


@dataclass(slots=True)
class SpendingReportData:
    """Input payload for spending report rendering."""

    period_label: str
    start_date: str
    end_date: str
    total: Decimal
    count: int
    average: Decimal
    currency: str
    categories: list[SpendingCategoryRow]
    transactions: list["SpendingTransactionRow"]
    transactions_truncated: bool = False
    transactions_unavailable: bool = False


@dataclass(slots=True)
class SpendingTransactionRow:
    """Spending transaction details row for detail pages."""

    date: str
    merchant: str
    category: str
    amount: Decimal


def _format_amount(value: Decimal, currency: str) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f} {currency}".replace(",", "'")


def _autopct_threshold(pct: float) -> str:
    return f"{pct:.1f}%" if pct >= 3 else ""


def _summarize_categories(categories: list[SpendingCategoryRow]) -> list[SpendingCategoryRow]:
    ordered = sorted(categories, key=lambda row: row.amount, reverse=True)
    top_rows = ordered[:8]
    other_total = sum((row.amount for row in ordered[8:]), Decimal("0"))
    if other_total > 0:
        top_rows.append(SpendingCategoryRow(name="Autres", amount=other_total))
    return top_rows


def _build_pie_chart(categories: list[SpendingCategoryRow]) -> bytes:
    rows = _summarize_categories(categories)
    labels = [row.name for row in rows]
    values = [float(row.amount) for row in rows]

    fig, ax = plt.subplots(figsize=(6.2, 3.6), dpi=140)
    wedges, _, _ = ax.pie(
        values,
        labels=None,
        autopct=_autopct_threshold,
        startangle=90,
        wedgeprops={"width": 0.45, "edgecolor": "white"},
        pctdistance=0.78,
    )
    ax.legend(
        wedges,
        labels,
        title="Catégories",
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        fontsize=8,
        frameon=False,
    )
    ax.set_title("Répartition par catégorie")
    ax.axis("equal")

    image_buffer = BytesIO()
    fig.savefig(image_buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    image_buffer.seek(0)
    return image_buffer.read()


class _FooterCanvas(Canvas):
    def __init__(self, *args, generated_on: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._generated_on = generated_on
        self._saved_page_states: list[dict] = []

    def showPage(self) -> None:  # noqa: N802 (ReportLab API)
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(page_count=page_count)
            super().showPage()
        super().save()

    def _draw_footer(self, *, page_count: int) -> None:
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#8A8F98"))
        self.drawString(20 * mm, 10 * mm, f"Généré le {self._generated_on}")
        self.drawRightString(190 * mm, 10 * mm, f"Page {self._pageNumber}/{page_count}")


def _build_kpi_cards(data: SpendingReportData) -> Table:
    styles = getSampleStyleSheet()
    card_style = ParagraphStyle(
        name="KpiCard",
        parent=styles["BodyText"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#1F2937"),
    )
    cells = [
        [
            Paragraph("<b>Total dépenses</b><br/>" + _format_amount(data.total, data.currency), card_style),
            Paragraph("<b>Nb opérations</b><br/>" + str(data.count), card_style),
            Paragraph("<b>Moyenne</b><br/>" + _format_amount(data.average, data.currency), card_style),
        ]
    ]
    table = Table(cells, colWidths=[58 * mm, 58 * mm, 58 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F6F8")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#DDE2E8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE2E8")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _build_transactions_table(data: SpendingReportData) -> Table:
    display_limit = 250

    def _truncate_text(value: str, max_length: int = 36) -> str:
        if len(value) <= max_length:
            return value
        return value[: max_length - 1].rstrip() + "…"

    table_data = [["Date", "Marchand", "Catégorie", "Montant"]]
    if data.transactions_unavailable:
        table_data.append(["-", "Détails indisponibles", "-", "-"])
    elif not data.transactions:
        table_data.append(["-", "Aucune transaction", "-", _format_amount(Decimal("0"), data.currency)])
    else:
        for row in data.transactions[:display_limit]:
            table_data.append(
                [
                    row.date,
                    _truncate_text(row.merchant),
                    row.category,
                    _format_amount(abs(row.amount), data.currency),
                ]
            )

    table = Table(table_data, colWidths=[28 * mm, 62 * mm, 58 * mm, 30 * mm], repeatRows=1)
    table_style: list[tuple] = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF1F4")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D7DCE2")),
        ("ALIGN", (3, 1), (3, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for row_index in range(1, len(table_data)):
        if row_index % 2 == 0:
            table_style.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#FAFBFC")))
    table.setStyle(TableStyle(table_style))
    return table


def generate_spending_report_pdf(data: SpendingReportData) -> bytes:
    """Render a 2-page spending report with summary and transaction detail table."""

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    section_title_style = ParagraphStyle(name="SectionTitle", parent=styles["Heading2"], spaceAfter=4, fontSize=12)
    subtitle_style = ParagraphStyle(name="Subtitle", parent=styles["BodyText"], fontSize=9, textColor=colors.HexColor("#6B7280"))

    story = [
        Paragraph("Rapport de dépenses", styles["Title"]),
        Spacer(1, 1 * mm),
        Paragraph(f"Période: {data.period_label}", styles["BodyText"]),
        Paragraph(f"Généré le {date.today().isoformat()}", subtitle_style),
        Spacer(1, 5 * mm),
        _build_kpi_cards(data),
        Spacer(1, 6 * mm),
    ]

    story.append(Paragraph("Répartition des dépenses", section_title_style))
    story.append(Spacer(1, 1 * mm))
    if not data.categories:
        story.append(Paragraph("Aucune transaction sur la période.", styles["BodyText"]))
    else:
        pie_bytes = _build_pie_chart(data.categories)
        story.append(Image(BytesIO(pie_bytes), width=166 * mm, height=92 * mm))
        story.append(Spacer(1, 3 * mm))

        total_categories = sum((row.amount for row in data.categories), Decimal("0"))
        top10 = sorted(data.categories, key=lambda row: row.amount, reverse=True)[:10]
        story.append(Paragraph("Top catégories", section_title_style))
        table_data = [["Catégorie", "Montant", "Part (%)"]]
        for row in top10:
            ratio = (row.amount / total_categories * Decimal("100")) if total_categories > 0 else Decimal("0")
            table_data.append([
                row.name,
                _format_amount(row.amount, data.currency),
                f"{ratio.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%",
            ])

        table = Table(table_data, colWidths=[90 * mm, 50 * mm, 20 * mm], repeatRows=1)
        table_style: list[tuple] = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF1F4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D7DCE2")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for row_index in range(1, len(table_data)):
            if row_index % 2 == 0:
                table_style.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#FAFBFC")))
        table.setStyle(TableStyle(table_style))
        story.append(table)

    story.append(PageBreak())
    story.append(Paragraph("Détail des transactions", styles["Title"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(f"Période: {data.period_label}", styles["BodyText"]))
    story.append(Spacer(1, 4 * mm))
    if data.transactions_truncated or len(data.transactions) > 250:
        story.append(Paragraph("Liste tronquée à 250 transactions (max 500 récupérées).", styles["Italic"]))
        story.append(Spacer(1, 2 * mm))
    if data.transactions_unavailable:
        story.append(Paragraph("Les détails des transactions sont indisponibles pour cette période.", styles["Italic"]))
        story.append(Spacer(1, 2 * mm))
    story.append(_build_transactions_table(data))

    generated_on = date.today().isoformat()
    doc.build(
        story,
        canvasmaker=lambda *args, **kwargs: _FooterCanvas(*args, generated_on=generated_on, **kwargs),
    )
    buffer.seek(0)
    return buffer.read()
