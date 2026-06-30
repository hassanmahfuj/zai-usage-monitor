"""Generate per-user usage report PDF for Z.ai usage data."""

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
    PageBreak,
)


def generate_user_pdf(
    df: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> bytes:
    """Generate a per-user usage PDF report.

    Creates one section per user with their KPIs, token type breakdown,
    model breakdown, and daily trend.

    Args:
        df: Filtered usage DataFrame.
        start_date: Report period start date.
        end_date: Report period end date.

    Returns:
        PDF file content as bytes.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("Z.ai Per-User Usage Report", styles["Title"]))
    elements.append(Spacer(1, 0.15 * inch))
    elements.append(
        Paragraph(
            f"Period: {start_date.strftime('%b %d, %Y')} — {end_date.strftime('%b %d, %Y')}",
            styles["Normal"],
        )
    )
    elements.append(Spacer(1, 0.3 * inch))

    users = sorted(df["username"].unique())

    for idx, user in enumerate(users):
        if idx > 0:
            elements.append(PageBreak())

        user_df = df[df["username"] == user]
        total_tokens = int(user_df["token_usage"].sum())
        total_cost = user_df["cost"].sum()
        total_requests = int(user_df["requests"].sum())

        elements.append(Paragraph(f"User: {user}", styles["Heading2"]))
        elements.append(Spacer(1, 0.15 * inch))

        # KPI table
        kpi_data = [
            ["Metric", "Value"],
            ["Total Tokens", f"{total_tokens:,}"],
            ["Total Cost", f"${total_cost:,.4f}"],
            ["Total Requests", f"{total_requests:,}"],
        ]
        kpi_table = Table(kpi_data, colWidths=[2.5 * inch, 3 * inch])
        kpi_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#D6E4F0")]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ])
        )
        elements.append(kpi_table)
        elements.append(Spacer(1, 0.25 * inch))

        # Token type breakdown
        elements.append(Paragraph("Token Breakdown", styles["Heading3"]))
        elements.append(Spacer(1, 0.1 * inch))

        type_agg = (
            user_df.groupby("token_type", as_index=False)
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
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#5B9BD5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#E9EFF7")]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
        )
        elements.append(type_table)
        elements.append(Spacer(1, 0.25 * inch))

        # Model breakdown
        elements.append(Paragraph("Model Breakdown", styles["Heading3"]))
        elements.append(Spacer(1, 0.1 * inch))

        model_agg = (
            user_df.groupby("model_code", as_index=False)
            .agg(tokens=("token_usage", "sum"), cost=("cost", "sum"), requests=("requests", "sum"))
            .sort_values("cost", ascending=False)
        )

        model_data = [["Model", "Tokens", "Cost ($)", "Requests"]]
        for _, row in model_agg.iterrows():
            model_data.append([
                str(row["model_code"]),
                f"{int(row['tokens']):,}",
                f"${row['cost']:,.4f}",
                f"{int(row['requests']):,}",
            ])

        model_table = Table(model_data, colWidths=[1.8 * inch, 1.5 * inch, 1.3 * inch, 1.2 * inch])
        model_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#5B9BD5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#E9EFF7")]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
        )
        elements.append(model_table)
        elements.append(Spacer(1, 0.25 * inch))

        # Daily trend
        elements.append(Paragraph("Daily Usage Trend", styles["Heading3"]))
        elements.append(Spacer(1, 0.1 * inch))

        daily = (
            user_df.groupby("billing_date", as_index=False)
            .agg(tokens=("token_usage", "sum"), cost=("cost", "sum"))
            .sort_values("billing_date")
        )

        daily_data = [["Date", "Tokens", "Cost ($)"]]
        for _, row in daily.iterrows():
            daily_data.append([
                str(row["billing_date"]),
                f"{int(row['tokens']):,}",
                f"${row['cost']:,.4f}",
            ])

        daily_table = Table(daily_data, colWidths=[2 * inch, 2 * inch, 2 * inch])
        daily_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#5B9BD5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#E9EFF7")]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
        )
        elements.append(daily_table)

    doc.build(elements)
    return buf.getvalue()
