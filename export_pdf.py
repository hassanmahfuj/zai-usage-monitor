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

from formatting import fmt_tokens


def _build_breakdown(
    df: pd.DataFrame,
    cdf: pd.DataFrame,
    label: str,
    group_col: str,
    styles,
    have_contrib: bool,
    show_sno: bool = False,
    sort_col: str = "cost",
) -> list:
    """Build a [Heading, Spacer, Table] flowable list for a breakdown table.

    Args:
        label: Display label for the group dimension (e.g. "Username").
        group_col: DataFrame column to group by ("username", "billing_date",
            or "model_code").
        styles: reportlab stylesheet (for the Heading2 paragraph).
        have_contrib: Whether cdf is non-empty (enables Contributions column).
        show_sno: Prepend an "S.No" rank column.
        sort_col: Column to sort descending by ("tokens" or "cost").
    """
    agg = (
        df.groupby(group_col, as_index=False)
        .agg(tokens=("token_usage", "sum"), cost=("cost", "sum"), requests=("requests", "sum"))
        .sort_values(sort_col, ascending=False)
    )

    # Contributions can be attributed for Username or Date, not Model.
    show_contrib_col = have_contrib and group_col in ("username", "billing_date")
    if show_contrib_col:
        if group_col == "username":
            contrib_agg = cdf.groupby("username", as_index=False).agg(contrib=("count", "sum"))
            agg = agg.merge(contrib_agg, left_on=group_col, right_on="username", how="left")
        else:  # billing_date
            contrib_agg = (
                cdf.groupby("contribution_date", as_index=False).agg(contrib=("count", "sum"))
            )
            agg = agg.merge(
                contrib_agg, left_on=group_col, right_on="contribution_date", how="left"
            )
        agg["contrib"] = agg["contrib"].fillna(0)

    header = []
    if show_sno:
        header.append("S.No")
    header.extend([label, "Tokens", "Cost ($)", "Requests"])
    if show_contrib_col:
        header.append("Contributions")

    data = [header]
    for idx, (_, row) in enumerate(agg.iterrows()):
        row_vals = []
        if show_sno:
            row_vals.append(str(idx + 1))
        row_vals.extend([
            str(row[group_col]),
            fmt_tokens(row['tokens']),
            f"${row['cost']:,.4f}",
            f"{int(row['requests']):,}",
        ])
        if show_contrib_col:
            row_vals.append(f"{int(row['contrib']):,}")
        data.append(row_vals)

    if show_sno and show_contrib_col:
        col_widths = [0.6 * inch, 1.7 * inch, 1.2 * inch, 1.1 * inch, 1.0 * inch, 1.1 * inch]
    elif show_sno:
        col_widths = [0.6 * inch, 2.0 * inch, 1.5 * inch, 1.3 * inch, 1.2 * inch]
    elif show_contrib_col:
        col_widths = [1.9 * inch, 1.3 * inch, 1.2 * inch, 1.1 * inch, 1.1 * inch]
    else:
        col_widths = [2.2 * inch, 1.5 * inch, 1.3 * inch, 1.2 * inch]

    table = Table(data, colWidths=col_widths)
    # Left-align the leading text columns (S.No + group label); right-align
    # the numeric columns. The group label sits at index 1 when S.No is shown.
    right_start = 2 if show_sno else 1
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("FONTSIZE", (0, 1), (-1, -1), 9),
            ("ALIGN", (0, 0), (right_start - 1, -1), "LEFT"),
            ("ALIGN", (right_start, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#D6E4F0")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )

    return [
        Paragraph(f"Breakdown by {label}", styles["Heading2"]),
        Spacer(1, 0.15 * inch),
        table,
    ]


def generate_summary_pdf(
    df: pd.DataFrame,
    start_date: date,
    end_date: date,
    group_by: str,
    cdf: pd.DataFrame | None = None,
) -> bytes:
    """Generate a PDF summary report.

    Args:
        df: Filtered usage DataFrame with columns including billing_date,
            username, model_code, token_type, token_usage, cost_price,
            requests, cost.
        start_date: Report period start date.
        end_date: Report period end date.
        group_by: Current group-by dimension (Date, Model, or Username).
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
    total_contrib = int(cdf["count"].sum()) if have_contrib else 0

    kpi_data = [
        ["Metric", "Value"],
        ["Total Tokens", fmt_tokens(total_tokens)],
        ["Total Cost", f"${total_cost:,.4f}"],
        ["Total Requests", f"{total_requests:,}"],
    ]
    if have_contrib:
        kpi_data.append(["Total Contributions", f"{total_contrib:,}"])
    kpi_data.append(["Records", f"{len(df):,}"])
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

    # --- Breakdown by {group_by} ---
    elements.append(Spacer(1, 0.4 * inch))
    group_map = {"Date": "billing_date", "Model": "model_code", "Username": "username"}
    group_col = group_map.get(group_by, "billing_date")
    show_sno = group_by == "Username"
    sort_col = "tokens" if show_sno else "cost"
    elements.extend(_build_breakdown(
        df, cdf, group_by, group_col, styles,
        have_contrib=have_contrib, show_sno=show_sno, sort_col=sort_col,
    ))

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
            fmt_tokens(row['tokens']),
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

    # --- Breakdown by Username (always present) ---
    # Skipped when group_by is already Username, to avoid duplicating the
    # table earlier. Ranked by token usage with S.No.
    if group_by != "Username":
        elements.append(Spacer(1, 0.4 * inch))
        elements.extend(_build_breakdown(
            df, cdf, "Username", "username", styles,
            have_contrib=have_contrib, show_sno=True, sort_col="tokens",
        ))

    doc.build(elements)
    return buf.getvalue()
