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

from formatting import fmt_tokens


def fit_widths(rel_widths, total):
    """Scale relative column widths so they sum exactly to `total` (page width)."""
    s = sum(rel_widths)
    return [total * r / s for r in rel_widths]


def build_user_detail_flowables(
    user: str,
    user_df: pd.DataFrame,
    cdf: pd.DataFrame,
    styles,
    have_contrib: bool,
    heading_text: str,
    usable_width: float,
    bookmark_key: str | None = None,
) -> list:
    """Build the flowables for one user's detail section.

    Args:
        user: Username (used to look up contributions).
        user_df: Filtered DataFrame for this user only.
        cdf: Contributions DataFrame (may be empty).
        styles: reportlab stylesheet.
        have_contrib: Whether cdf is non-empty.
        heading_text: Text for the section heading (e.g. "User: bob" or "3.1  BOB").
        bookmark_key: If given, the heading is tagged so a TableOfContents /
            PDF outline entry can be registered by the doc's afterFlowable hook.

    Returns:
        List of platypus flowables (heading + KPI table + token-type breakdown
        + model breakdown + daily trend). No leading PageBreak.
    """
    total_tokens = int(user_df["token_usage"].sum())
    total_cost = user_df["cost"].sum()
    total_requests = int(user_df["requests"].sum())
    user_contrib = (
        int(cdf.loc[cdf["username"] == user, "count"].sum()) if have_contrib else 0
    )

    heading = Paragraph(heading_text, styles["Heading2"])
    if bookmark_key:
        heading._toc_key = bookmark_key
        heading._toc_level = 1
    flowables = [heading, Spacer(1, 0.15 * inch)]

    # KPI table
    kpi_data = [
        ["Metric", "Value"],
        ["Total Tokens", fmt_tokens(total_tokens)],
        ["Total Cost", f"${total_cost:,.4f}"],
        ["Total Requests", f"{total_requests:,}"],
    ]
    if have_contrib:
        kpi_data.append(["Total Contributions", f"{user_contrib:,}"])
    kpi_table = Table(kpi_data, colWidths=fit_widths([2.5, 3], usable_width))
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
    flowables.append(kpi_table)
    flowables.append(Spacer(1, 0.25 * inch))

    # Token type breakdown
    flowables.append(Paragraph("Token Breakdown", styles["Heading3"]))
    flowables.append(Spacer(1, 0.1 * inch))

    type_agg = (
        user_df.groupby("token_type", as_index=False)
        .agg(tokens=("token_usage", "sum"), cost=("cost", "sum"))
        .sort_values("tokens", ascending=False)
    )

    type_data = [["Token Type", "Tokens", "Cost ($)"]]
    for _, row in type_agg.iterrows():
        type_data.append([
            str(row["token_type"]),
            fmt_tokens(row['tokens']),
            f"${row['cost']:,.4f}",
        ])

    type_table = Table(type_data, colWidths=fit_widths([1, 1, 1], usable_width))
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
    flowables.append(type_table)
    flowables.append(Spacer(1, 0.25 * inch))

    # Model breakdown
    flowables.append(Paragraph("Model Breakdown", styles["Heading3"]))
    flowables.append(Spacer(1, 0.1 * inch))

    model_agg = (
        user_df.groupby("model_code", as_index=False)
        .agg(tokens=("token_usage", "sum"), cost=("cost", "sum"), requests=("requests", "sum"))
        .sort_values("cost", ascending=False)
    )

    model_data = [["Model", "Tokens", "Cost ($)", "Requests"]]
    for _, row in model_agg.iterrows():
        model_data.append([
            str(row["model_code"]),
            fmt_tokens(row['tokens']),
            f"${row['cost']:,.4f}",
            f"{int(row['requests']):,}",
        ])

    model_table = Table(model_data, colWidths=fit_widths([1.8, 1.5, 1.3, 1.2], usable_width))
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
    flowables.append(model_table)
    flowables.append(Spacer(1, 0.25 * inch))

    # Daily trend
    flowables.append(Paragraph("Daily Usage Trend", styles["Heading3"]))
    flowables.append(Spacer(1, 0.1 * inch))

    daily = (
        user_df.groupby("billing_date", as_index=False)
        .agg(tokens=("token_usage", "sum"), cost=("cost", "sum"))
        .sort_values("billing_date")
    )

    show_daily_contrib = have_contrib
    if show_daily_contrib:
        user_daily_contrib = (
            cdf[cdf["username"] == user]
            .groupby("contribution_date", as_index=False)
            .agg(contrib=("count", "sum"))
        )
        daily = daily.merge(
            user_daily_contrib,
            left_on="billing_date",
            right_on="contribution_date",
            how="left",
        )
        daily["contrib"] = daily["contrib"].fillna(0)

    daily_header = ["Date", "Tokens", "Cost ($)"]
    if show_daily_contrib:
        daily_header.append("Contributions")

    daily_data = [daily_header]
    for _, row in daily.iterrows():
        row_vals = [
            str(row["billing_date"]),
            fmt_tokens(row['tokens']),
            f"${row['cost']:,.4f}",
        ]
        if show_daily_contrib:
            row_vals.append(f"{int(row['contrib']):,}")
        daily_data.append(row_vals)

    daily_widths = fit_widths(
        [1.7, 1.7, 1.5, 1.4] if show_daily_contrib else [1, 1, 1],
        usable_width,
    )
    daily_table = Table(daily_data, colWidths=daily_widths)
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
    flowables.append(daily_table)

    return flowables


def generate_user_pdf(
    df: pd.DataFrame,
    start_date: date,
    end_date: date,
    cdf: pd.DataFrame | None = None,
) -> bytes:
    """Generate a per-user usage PDF report.

    Creates one section per user with their KPIs, token type breakdown,
    model breakdown, and daily trend.

    Args:
        df: Filtered usage DataFrame.
        start_date: Report period start date.
        end_date: Report period end date.
        cdf: Optional filtered contributions DataFrame with columns
            username, contribution_date, count. When empty/None, no
            contribution rows/columns are added.

    Returns:
        PDF file content as bytes.
    """
    if cdf is None:
        cdf = pd.DataFrame()
    have_contrib = not cdf.empty

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
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
        elements.extend(
            build_user_detail_flowables(
                user, user_df, cdf, styles, have_contrib, f"User: {user}",
                usable_width=doc.width,
            )
        )

    doc.build(elements)
    return buf.getvalue()
