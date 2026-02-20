"""Generate spending report PDFs for finance endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


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


def _format_amount(value: Decimal, currency: str) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f} {currency}".replace(",", "'")


def _build_pie_chart(categories: list[SpendingCategoryRow]) -> bytes:
    ordered = sorted(categories, key=lambda row: row.amount, reverse=True)
    top_rows = ordered[:8]
    other_total = sum((row.amount for row in ordered[8:]), Decimal("0"))

    labels = [row.name for row in top_rows]
    values = [float(row.amount) for row in top_rows]
    if other_total > 0:
        labels.append("Autres")
        values.append(float(other_total))

    fig, ax = plt.subplots(figsize=(5, 3.2), dpi=140)
    ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
    ax.set_title("Répartition par catégorie")
    ax.axis("equal")

    image_buffer = BytesIO()
    fig.savefig(image_buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    image_buffer.seek(0)
    return image_buffer.read()


def generate_spending_report_pdf(data: SpendingReportData) -> bytes:
    """Render a minimal PDF spending report from aggregated data."""

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Rapport dépenses", styles["Title"]),
        Spacer(1, 4 * mm),
        Paragraph(f"Période: {data.period_label}", styles["Normal"]),
        Spacer(1, 3 * mm),
        Paragraph(f"Total dépenses: {_format_amount(data.total, data.currency)}", styles["Normal"]),
        Paragraph(f"Nombre d'opérations: {data.count}", styles["Normal"]),
        Paragraph(f"Moyenne: {_format_amount(data.average, data.currency)}", styles["Normal"]),
        Spacer(1, 5 * mm),
    ]

    if not data.categories:
        story.append(Paragraph("Aucune transaction sur la période.", styles["BodyText"]))
    else:
        pie_bytes = _build_pie_chart(data.categories)
        story.append(Image(BytesIO(pie_bytes), width=130 * mm, height=82 * mm))
        story.append(Spacer(1, 4 * mm))

        total_categories = sum((row.amount for row in data.categories), Decimal("0"))
        top10 = sorted(data.categories, key=lambda row: row.amount, reverse=True)[:10]
        table_data = [["Catégorie", "Montant", "%"]]
        for row in top10:
            ratio = (row.amount / total_categories * Decimal("100")) if total_categories > 0 else Decimal("0")
            table_data.append([
                row.name,
                _format_amount(row.amount, data.currency),
                f"{ratio.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%",
            ])

        table = Table(table_data, colWidths=[75 * mm, 45 * mm, 20 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#efefef")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ]
            )
        )
        story.append(table)

    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Note: Certaines catégories peuvent être exclues des totaux.", styles["Italic"]))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()
