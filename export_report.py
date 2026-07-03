"""Generate the combined Z.ai usage report PDF (summary + per-user + TOC)."""

import io
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

from export_user import build_user_detail_flowables, fit_widths
from formatting import fmt_tokens

# Figure size (inches) for the Tokens vs Contributions chart — used to keep
# the embedded image's aspect ratio in sync with the PDF column width.
_CHART_FIG_W = 7.0
_CHART_FIG_H = 3.2


class ReportDocTemplate(BaseDocTemplate):
    """Doc template that registers TOC entries + PDF outline bookmarks."""

    def __init__(self, filename, **kw):
        super().__init__(filename, **kw)
        frame = Frame(
            self.leftMargin, self.bottomMargin, self.width, self.height, id="main"
        )
        self.addPageTemplates([PageTemplate(id="main", frames=[frame])])

    def afterFlowable(self, flowable):
        key = getattr(flowable, "_toc_key", None)
        if not key:
            return
        text = flowable.getPlainText()
        level = getattr(flowable, "_toc_level", 0)
        self.notify("TOCEntry", (level, text, self.page, key))
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level=level, closed=0)


def _section_heading(text: str, styles, key: str) -> Paragraph:
    """A level-0 (section) heading tagged for the TOC and PDF outline."""
    p = Paragraph(text, styles["Heading1"])
    p._toc_key = key
    p._toc_level = 0
    return p


def _kpi_table_style() -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 11),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#D6E4F0")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])


def _tokens_vs_contrib_png(df: pd.DataFrame, cdf: pd.DataFrame) -> bytes:
    """Render the Tokens vs Contributions chart to PNG bytes.

    Dual-axis grouped bars per user: token usage (left, K/M/B formatted) and
    contributions (right). Mirrors the dashboard's Plotly chart.
    """
    tokens = df.groupby("username", as_index=False)["token_usage"].sum()
    contrib = cdf.groupby("username", as_index=False)["count"].sum()
    merged = tokens.merge(contrib, on="username", how="outer").fillna(0)
    merged = merged.sort_values("token_usage", ascending=False).reset_index(drop=True)

    users = merged["username"].tolist()
    x = range(len(users))
    w = 0.4

    fig, ax1 = plt.subplots(figsize=(_CHART_FIG_W, _CHART_FIG_H))
    ax2 = ax1.twinx()

    bars_tok = ax1.bar(
        [i - w / 2 for i in x], merged["token_usage"], width=w,
        color="#636EFA", label="Tokens",
    )
    ax2.bar(
        [i + w / 2 for i in x], merged["count"], width=w,
        color="#19A3A3", label="Contributions",
    )

    ax1.set_xticks(list(x))
    ax1.set_xticklabels(users, rotation=45, ha="right")
    ax1.set_xlabel("Username")
    ax1.set_ylabel("Tokens")
    ax2.set_ylabel("Contributions")
    ax1.set_title("Tokens vs Contributions")
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_tokens(v)))

    fig.legend(
        handles=[bars_tok],
        labels=["Tokens"],
        loc="upper left",
        bbox_to_anchor=(0.12, 0.9),
    )
    fig.legend(
        handles=[ax2.containers[0]],
        labels=["Contributions"],
        loc="upper right",
        bbox_to_anchor=(0.88, 0.9),
    )

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def generate_report_pdf(
    df: pd.DataFrame,
    start_date: date,
    end_date: date,
    cdf: pd.DataFrame | None = None,
) -> bytes:
    """Generate the combined usage PDF: TOC + Overall Summary + User Breakdown
    + Detailed Model Usage per User.

    Args:
        df: Filtered usage DataFrame with billing_date, username, model_code,
            token_type, token_usage, cost_price, requests, cost.
        start_date: Report period start date.
        end_date: Report period end date.
        cdf: Optional filtered contributions DataFrame (username,
            contribution_date, count).

    Returns:
        PDF file content as bytes.
    """
    if cdf is None:
        cdf = pd.DataFrame()
    have_contrib = not cdf.empty

    buf = io.BytesIO()
    doc = ReportDocTemplate(
        buf, pagesize=A4,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
    styles = getSampleStyleSheet()
    elements = []
    usable = doc.width

    # ------------------------------------------------------------------ Page 1
    elements.append(Paragraph("Z.ai Usage Report", styles["Title"]))
    elements.append(Spacer(1, 0.05 * inch))
    elements.append(Paragraph("Automated Analysis", styles["Italic"]))
    elements.append(Spacer(1, 0.05 * inch))
    elements.append(
        Paragraph(
            f"Period: {start_date.strftime('%b %d, %Y')} — {end_date.strftime('%b %d, %Y')}",
            styles["Normal"],
        )
    )
    elements.append(Spacer(1, 0.3 * inch))

    elements.append(Paragraph("Table of Contents", styles["Heading2"]))
    elements.append(Spacer(1, 0.15 * inch))
    toc = TableOfContents()
    toc.dotsMinLevel = 0
    toc.levelStyles = [
        ParagraphStyle(
            "TOC1", fontName="Helvetica-Bold", fontSize=12, leftIndent=0,
            firstLineIndent=0, spaceBefore=6,
        ),
        ParagraphStyle(
            "TOC2", fontName="Helvetica", fontSize=11, leftIndent=20,
            firstLineIndent=0, spaceBefore=2,
        ),
    ]
    elements.append(toc)
    elements.append(PageBreak())

    # ------------------------------------------------------- Section 1: Summary
    elements.append(_section_heading("1  Overall Summary", styles, "s1"))
    elements.append(Spacer(1, 0.1 * inch))

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
    kpi_table = Table(kpi_data, colWidths=fit_widths([2.5, 3], usable))
    kpi_table.setStyle(_kpi_table_style())
    elements.append(kpi_table)
    elements.append(Spacer(1, 0.3 * inch))

    # -------------------------------------------------- Section 2: User Breakdown
    elements.append(_section_heading("2  User Breakdown", styles, "s2"))
    elements.append(Spacer(1, 0.1 * inch))

    user_agg = (
        df.groupby("username", as_index=False)
        .agg(tokens=("token_usage", "sum"), cost=("cost", "sum"), requests=("requests", "sum"))
    )

    # Top model per user = model with the highest total token_usage.
    model_by_user = (
        df.groupby(["username", "model_code"], as_index=False)["token_usage"].sum()
    )
    top_model = (
        model_by_user.sort_values("token_usage", ascending=False)
        .drop_duplicates("username")
        .set_index("username")["model_code"]
    )
    user_agg["top_model"] = user_agg["username"].map(top_model)

    if have_contrib:
        contrib = cdf.groupby("username", as_index=False).agg(contrib=("count", "sum"))
        user_agg = user_agg.merge(contrib, on="username", how="left")
        user_agg["contrib"] = user_agg["contrib"].fillna(0).astype(int)

    user_agg = user_agg.sort_values("tokens", ascending=False).reset_index(drop=True)

    header = ["User", "Requests", "Tokens", "Cost ($)", "Top Model"]
    if have_contrib:
        header.append("Contributions")
    rows = [header]
    for _, row in user_agg.iterrows():
        r = [
            str(row["username"]),
            f"{int(row['requests']):,}",
            fmt_tokens(row['tokens']),
            f"${row['cost']:,.4f}",
            str(row["top_model"]),
        ]
        if have_contrib:
            r.append(f"{int(row['contrib']):,}")
        rows.append(r)

    col_widths = fit_widths(
        [1.35, 0.85, 1.05, 0.95, 1.15] + ([0.95] if have_contrib else []),
        usable,
    )
    ub_table = Table(rows, colWidths=col_widths)
    ub_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("ALIGN", (1, 0), (3, -1), "RIGHT"),
        ("ALIGN", (4, 0), (4, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#D6E4F0")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if have_contrib:
        ub_style.append(("ALIGN", (5, 0), (5, -1), "RIGHT"))
    ub_table.setStyle(TableStyle(ub_style))
    elements.append(ub_table)

    if have_contrib:
        elements.append(Spacer(1, 0.25 * inch))
        elements.append(
            Paragraph("Tokens vs Contributions", styles["Heading3"])
        )
        elements.append(Spacer(1, 0.1 * inch))
        _chart_png = _tokens_vs_contrib_png(df, cdf)
        elements.append(
            Image(
                io.BytesIO(_chart_png),
                width=usable,
                height=usable * _CHART_FIG_H / _CHART_FIG_W,
            )
        )

    elements.append(PageBreak())

    # --------------------------------------- Section 3: Detailed per-user pages
    elements.append(_section_heading("3  Detailed Model Usage per User", styles, "s3"))

    users = (
        df.groupby("username", as_index=False)["token_usage"].sum()
        .sort_values("token_usage", ascending=False)["username"]
        .tolist()
    )
    for i, user in enumerate(users, 1):
        if i > 1:
            elements.append(PageBreak())
        user_df = df[df["username"] == user]
        elements.extend(
            build_user_detail_flowables(
                user, user_df, cdf, styles, have_contrib,
                heading_text=f"3.{i}  {user}", usable_width=usable,
                bookmark_key=f"u{i}",
            )
        )

    doc.multiBuild(elements)
    return buf.getvalue()
