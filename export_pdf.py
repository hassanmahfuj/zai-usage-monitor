"""Generate PDF summary report for Z.ai usage data."""

import io
from datetime import date

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)


def generate_summary_pdf(
    df: pd.DataFrame,
    start_date: date,
    end_date: date,
    group_by: str,
) -> bytes:
    """Generate a PDF summary report.

    Args:
        df: Filtered usage DataFrame with columns including billing_date,
            username, model_code, token_type, token_usage, cost_price,
            requests, cost.
        start_date: Report period start date.
        end_date: Report period end date.
        group_by: Current group-by dimension (Date, Model, or Username).

    Returns:
        PDF file content as bytes.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    elements = []

    # --- Page 1: Title & KPIs ---
    elements.append(Paragraph("Z.ai Usage Report", styles["Title"]))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(
        Paragraph(
            f"Period: {start_date.strftime('%b %d, %Y')} — {end_date.strftime('%b %d, %Y')}",
            styles["Normal"],
        )
    )
    elements.append(Spacer(1, 0.3 * inch))

    # KPI summary table
    total_tokens = int(df["token_usage"].sum())
    total_cost = df["cost"].sum()
    total_requests = int(df["requests"].sum())

    kpi_data = [
        ["Metric", "Value"],
        ["Total Tokens", f"{total_tokens:,}"],
        ["Total Cost", f"${total_cost:,.4f}"],
        ["Total Requests", f"{total_requests:,}"],
        ["Records", f"{len(df):,}"],
    ]
    kpi_table = Table(kpi_data, colWidths=[2.5 * inch, 3 * inch])
    kpi_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, 0), 12),
            ("FONTSIZE", (0, 1), (-1, -1), 11),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#D6E4F0")]),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    elements.append(kpi_table)

    # --- Page 2: Group-by summary ---
    elements.append(Spacer(1, 0.4 * inch))
    elements.append(Paragraph(f"Breakdown by {group_by}", styles["Heading2"]))
    elements.append(Spacer(1, 0.15 * inch))

    group_map = {"Date": "billing_date", "Model": "model_code", "Username": "username"}
    group_col = group_map.get(group_by, "billing_date")

    agg = (
        df.groupby(group_col, as_index=False)
        .agg(tokens=("token_usage", "sum"), cost=("cost", "sum"), requests=("requests", "sum"))
        .sort_values("cost", ascending=False)
    )

    summary_data = [[group_by, "Tokens", "Cost ($)", "Requests"]]
    for _, row in agg.iterrows():
        summary_data.append([
            str(row[group_col]),
            f"{int(row['tokens']):,}",
            f"${row['cost']:,.4f}",
            f"{int(row['requests']):,}",
        ])

    summary_table = Table(summary_data, colWidths=[2.2 * inch, 1.5 * inch, 1.3 * inch, 1.2 * inch])
    summary_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("FONTSIZE", (0, 1), (-1, -1), 9),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#D6E4F0")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    elements.append(summary_table)

    # --- Page 3: Token type breakdown ---
    elements.append(Spacer(1, 0.4 * inch))
    elements.append(Paragraph("Token Type Breakdown", styles["Heading2"]))
    elements.append(Spacer(1, 0.15 * inch))

    type_agg = (
        df.groupby("token_type", as_index=False)
        .agg(tokens=("token_usage", "sum"), cost=("cost", "sum"))
        .sort_values("tokens", ascending=False)
    )

    type_data = [["Token Type", "Tokens", "Cost ($)"]]
    for _, row in type_agg.iterrows():
        type_data.append([
            str(row["token_type"]),
            f"{int(row['tokens']):,}",
            f"${row['cost']:,.4f}",
        ])

    type_table = Table(type_data, colWidths=[2 * inch, 2 * inch, 2 * inch])
    type_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("FONTSIZE", (0, 1), (-1, -1), 9),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#D6E4F0")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    elements.append(type_table)

    doc.build(elements)
    return buf.getvalue()
