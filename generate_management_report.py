"""Standalone management report generator (Feb 1 - Jun 30, 2026).

Reads ``data/billing.db`` directly via sqlite3 and produces a single PDF:
``zai_management_report_feb-jun_2026.pdf``.

- Does NOT edit any existing project file.
- Does NOT use the contributions table (per requirement: cost-effectiveness is
  framed purely as subscription cost vs usage value).
- Self-contained: run with ``python generate_management_report.py``.
"""

import io
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from formatting import fmt_tokens

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).parent / "data" / "billing.db"
START_DATE = "2026-02-01"
END_DATE = "2026-06-30"
PACKAGE_COST = 80.0
OUTPUT_PATH = Path(__file__).parent / "zai_management_report_feb-jun_2026.pdf"

# Palette
C_PRIMARY = "#636EFA"
C_ACCENT = "#00CC96"
C_COST = "#EF553B"
C_AMBER = "#FFA15A"
C_PURPLE = "#AB63FA"
C_TEAL = "#19A3A3"
C_DARK = "#0F172A"
C_MUTED = "#64748B"
C_GRID = "#E2E8F0"
C_LIGHT = "#F1F5F9"


# ---------------------------------------------------------------------------
# Success stories (from the manager's collected feedback)
# ---------------------------------------------------------------------------
# (engineer, initiative, est_days, actual_days, display_est)
SUCCESS_QUANT = [
    ("Amirul", "eKYC app — image optimization & refactoring", 15.0, 3.0, "15 days"),
    ("Amirul", "Transaction history — report design & download", 5.0, 2.0, "5 days"),
    ("Amirul", "Security — developer option check", 5.0, 2.0, "5 days"),
    ("Mahfuz", "Adding new event to inv batch process", 4.0, 1.0, "4 days"),
    ("Mahfuz", "Writeoff waive correction — 4 UIs and service development", 5.0, 2.0, "5 days"),
]

SUCCESS_CARDS = [
    ("Shehroze",
     "In-house tools developed rapidly and easily; codebase analysis "
     "delivered significant time reduction."),
    ("Mahfuz",
     "Built productivity-booster scripts for easier deployment, development "
     "and exploration; code exploration and research improved."),
]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_usage() -> pd.DataFrame:
    """Load usage records joined with usernames for the configured period."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        df = pd.read_sql_query(
            "SELECT b.billing_date, b.model_code, b.token_usage, b.requests, "
            "b.cost_price * b.token_usage / 1000.0 AS cost, "
            "COALESCE(m.username, 'Unknown') AS username "
            "FROM usage_records b "
            "LEFT JOIN api_key_map m ON b.api_key = m.api_key "
            "WHERE b.billing_date BETWEEN ? AND ?",
            conn, params=[START_DATE, END_DATE],
        )
    finally:
        conn.close()
    return df


def compute_metrics(df: pd.DataFrame, package_cost: float = PACKAGE_COST) -> dict:
    total_tokens = int(df["token_usage"].sum())
    total_requests = int(df["requests"].sum())
    list_value = float(df["cost"].sum())
    active_users = int(df["username"].nunique())

    monthly = (df.assign(month=df["billing_date"].astype(str).str[:7])
                 .groupby("month")
                 .agg(requests=("requests", "sum"),
                      tokens=("token_usage", "sum"),
                      cost=("cost", "sum"),
                      active=("username", "nunique"))
                 .reset_index())

    first_m = monthly.iloc[0]["month"] if len(monthly) else ""
    last_m = monthly.iloc[-1]["month"] if len(monthly) else ""
    req_growth = (
        (monthly.iloc[-1]["requests"] / monthly.iloc[0]["requests"] - 1) * 100
        if len(monthly) > 1 and monthly.iloc[0]["requests"] > 0 else 0.0
    )

    n_months = len(monthly) if len(monthly) else 1
    monthly_sub = package_cost / n_months
    roi_mult = list_value / package_cost if package_cost > 0 else 0.0
    eff_per_1m = package_cost / (total_tokens / 1_000_000) if total_tokens else 0.0
    eff_per_req = package_cost / total_requests if total_requests else 0.0
    eff_per_user_month = (package_cost / active_users / n_months) if active_users else 0.0

    return dict(
        total_tokens=total_tokens, total_requests=total_requests,
        list_value=list_value, active_users=active_users,
        first_m=first_m, last_m=last_m, req_growth=req_growth,
        n_months=n_months, monthly_sub=monthly_sub, roi_mult=roi_mult,
        eff_per_1m=eff_per_1m, eff_per_req=eff_per_req,
        eff_per_user_month=eff_per_user_month, monthly=monthly,
    )


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------
def _style_axes(ax) -> None:
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(C_GRID)
    ax.spines["bottom"].set_color(C_GRID)
    ax.tick_params(colors=C_MUTED, labelsize=10)
    ax.yaxis.grid(True, color=C_GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _save(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def chart_active_users(df: pd.DataFrame) -> bytes:
    s = df.assign(month=df["billing_date"].astype(str).str[:7])
    months = sorted(s["month"].unique())
    new_u, ret_u, total = [], [], []
    seen: set[str] = set()
    for m in months:
        cur = set(s.loc[s["month"] == m, "username"].unique())
        new_u.append(len(cur - seen))
        ret_u.append(len(cur & seen))
        total.append(len(cur))
        seen |= cur
    fig, ax = plt.subplots(figsize=(11.5, 3.8))
    ax.bar(months, new_u, color=C_ACCENT, label="New", width=0.6)
    ax.bar(months, ret_u, bottom=new_u, color=C_PRIMARY, label="Returning", width=0.6)
    ax.plot(months, total, color=C_DARK, marker="o", linewidth=2.4,
            markersize=7, label="Total active")
    for i, t in enumerate(total):
        ax.text(i, t + 0.3, f"{t}", ha="center", va="bottom",
                fontsize=10, fontweight="bold", color=C_DARK)
    _style_axes(ax)
    ax.set_title("Active Users per Month", color=C_DARK, fontsize=13, fontweight="bold")
    ax.set_ylabel("Users")
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.set_ylim(0, max(total) * 1.25)
    fig.tight_layout()
    return _save(fig)


def chart_top_users(df: pd.DataFrame, n: int = 10) -> bytes:
    """Top N users by tokens (bar = tokens, annotated with cost + requests)."""
    g = (df.groupby("username").agg(
            cost=("cost", "sum"), tokens=("token_usage", "sum"),
            requests=("requests", "sum")).reset_index()
           .sort_values("tokens", ascending=False).head(n).iloc[::-1])
    fig, ax = plt.subplots(figsize=(11.5, 5.0))
    ax.barh(g["username"], g["tokens"], color=C_PRIMARY, height=0.62)
    xmax = g["tokens"].max()
    for i, (_, row) in enumerate(g.iterrows()):
        ax.text(row["tokens"], i,
                f"  ${row['cost']:.0f} · {int(row['requests']/1000)}K req",
                va="center", ha="left", fontsize=10, color=C_DARK)
    _style_axes(ax)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=11)
    ax.set_title(f"Top {n} Users by Tokens", color=C_DARK, fontsize=13,
                 fontweight="bold", pad=10)
    ax.set_xlabel("Tokens")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_tokens(v)))
    ax.set_xlim(0, xmax * 1.55)
    fig.tight_layout()
    return _save(fig)


def chart_cost_effectiveness(monthly: pd.DataFrame, monthly_sub: float) -> bytes:
    fig, ax = plt.subplots(figsize=(11.5, 3.8))
    ax.bar(monthly["month"], monthly["cost"], color=C_PRIMARY, width=0.55,
           label="Usage value consumed (USD)")
    ax.axhline(monthly_sub, color=C_COST, linewidth=2.2, linestyle="--",
               label=f"Subscription allocation (${monthly_sub:.0f}/mo)")
    _style_axes(ax)
    ax.set_title("Monthly Usage Value vs Subscription Cost",
                 color=C_DARK, fontsize=13, fontweight="bold", pad=10)
    ax.set_ylabel("USD")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:.0f}"))
    ax.legend(loc="upper left", frameon=False, fontsize=8.5)
    fig.tight_layout()
    return _save(fig)


# ---------------------------------------------------------------------------
# PDF building blocks
# ---------------------------------------------------------------------------
def _build_styles() -> dict:
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=ss["Title"], fontSize=26,
                                textColor=colors.HexColor(C_DARK), spaceAfter=4,
                                leading=30),
        "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], fontSize=13,
                                   textColor=colors.HexColor(C_MUTED), spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=17,
                             textColor=colors.HexColor(C_DARK), spaceAfter=10,
                             spaceBefore=4),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=11,
                               textColor=colors.HexColor("#1E293B"), leading=16),
        "takeaway": ParagraphStyle("takeaway", parent=ss["Normal"], fontSize=12,
                                   textColor=colors.HexColor(C_DARK), leading=17,
                                   alignment=1, spaceBefore=4),
        "tile_big": ParagraphStyle("tile_big", parent=ss["Normal"], fontSize=24,
                                   textColor=colors.white, alignment=1, leading=26,
                                   fontName="Helvetica-Bold"),
        "tile_small": ParagraphStyle("tile_small", parent=ss["Normal"], fontSize=8.5,
                                     textColor=colors.HexColor("#E2E8F0"), alignment=1,
                                     leading=11),
        "card_name": ParagraphStyle("card_name", parent=ss["Normal"], fontSize=12,
                                    textColor=colors.HexColor(C_DARK), leading=15,
                                    fontName="Helvetica-Bold"),
        "card_body": ParagraphStyle("card_body", parent=ss["Normal"], fontSize=10,
                                    textColor=colors.HexColor("#334155"), leading=14),
        "headline": ParagraphStyle("headline", parent=ss["Normal"], fontSize=13,
                                   textColor=colors.HexColor(C_DARK), leading=18,
                                   fontName="Helvetica-Bold", spaceAfter=10),
    }


def _tile(big: str, small: str, bg: str, styles: dict, width: float) -> Table:
    inner = Table(
        [[Paragraph(big, styles["tile_big"])],
         [Paragraph(small, styles["tile_small"])]],
        colWidths=[width],
    )
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    return inner


def _img(png: bytes, width: float) -> Image:
    img = Image(io.BytesIO(png))
    img._restrictSize(width, 12 * cm)
    return img


# ---------------------------------------------------------------------------
# Page builders
# ---------------------------------------------------------------------------
def page1_cover(m: dict, styles: dict, usable: float,
                start_date: str = START_DATE, end_date: str = END_DATE,
                package_cost: float = PACKAGE_COST) -> list:
    out = []
    out.append(Paragraph("Z.ai Usage &amp; Cost-Effectiveness Report", styles["title"]))
    out.append(Paragraph(
        f"Period: {start_date} to {end_date}  ·  Team engineering review",
        styles["subtitle"]))
    # out.append(Spacer(1, 0.3 * cm))

    tw = (usable - 0.9 * cm) / 4
    tiles = [[
        _tile(f"{m['roi_mult']:.0f}×",
              f"on ${package_cost:.0f} ${m['list_value']:,.0f} value",
              C_ACCENT, styles, tw),
        _tile(f"{m['total_requests']/1000:.0f}K", "API requests",
              C_PRIMARY, styles, tw),
        _tile(fmt_tokens(m["total_tokens"]), "tokens",
              C_PURPLE, styles, tw),
        _tile(f"{m['active_users']}", "engineers",
              C_AMBER, styles, tw),
    ]]
    grid = Table(tiles, colWidths=[tw, tw, tw, tw])
    grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    out.append(grid)
    # out.append(Spacer(1, 0.3 * cm))

    takeaway = (
        f"For a <b>${package_cost:.0f}</b> subscription, the team consumed "
        f"<b>${m['list_value']:,.0f}</b> of AI compute value "
        f"(<b>{m['roi_mult']:.0f}× return</b>), driving "
        f"<b>{m['req_growth']:.0f}% request growth</b> "
        f"({m['first_m']} → {m['last_m']}) across {m['active_users']} engineers."
    )
    box = Table([[Paragraph(takeaway, styles["takeaway"])]], colWidths=[usable])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(C_LIGHT)),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(C_GRID)),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    out.append(box)
    out.append(Spacer(1, 0.35 * cm))
    return out


def page2_charts(df: pd.DataFrame, m: dict, styles: dict, usable: float) -> list:
    out = [Paragraph("Usage Overview", styles["h2"])]

    # Active users (full width)
    out.append(_img(chart_active_users(df), usable))
    # out.append(Spacer(1, 0.3 * cm))

    # Cost effectiveness (full width)
    out.append(_img(chart_cost_effectiveness(m["monthly"], m["monthly_sub"]), usable))
    # out.append(Spacer(1, 0.3 * cm))

    # Top users by tokens (full width)
    out.append(_img(chart_top_users(df), usable))
    return out


def page3_success(styles: dict, usable: float) -> list:
    out = [Paragraph("Success Stories", styles["h2"])]

    total_saved = sum(e - a for _, _, e, a, _ in SUCCESS_QUANT)
    out.append(Paragraph(
        f"~{total_saved:.0f} developer-days saved across "
        f"{len(SUCCESS_QUANT)} quantified tasks — a sample of the team's "
        f"AI-accelerated delivery.",
        styles["headline"]))
    out.append(Spacer(1, 0.15 * cm))

    # Quantified table
    header = ["Engineer", "Initiative", "Without AI", "With AI", "Saved"]
    rows = [header]
    for eng, init, est, act, disp_est in SUCCESS_QUANT:
        saved = (est - act) / est * 100 if est else 0
        act_label = f"{act:.0f} day" if act == 1 else f"{act:.0f} days"
        rows.append([eng, init, disp_est, act_label, f"{saved:.0f}%"])
    # totals row
    sum_est = sum(e for _, _, e, _, _ in SUCCESS_QUANT)
    sum_act = sum(a for _, _, _, a, _ in SUCCESS_QUANT)
    rows.append(["", "Total", f"{sum_est:.0f} days", f"{sum_act:.0f} days",
                 f"{(sum_est - sum_act) / sum_est * 100:.0f}%"])

    col_w = [2.0 * cm, 7.6 * cm, 2.3 * cm, 2.0 * cm, 1.6 * cm]
    # scale to usable
    scale = usable / sum(col_w)
    col_w = [w * scale for w in col_w]
    tbl = Table(rows, colWidths=col_w)
    n_rows = len(rows)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_PRIMARY)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("FONTSIZE", (0, 1), (-1, -1), 9.5),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(C_GRID)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2),
         [colors.white, colors.HexColor(C_LIGHT)]),
        # totals row
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DBEAFE")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (4, 1), (4, -1), colors.HexColor(C_ACCENT)),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    out.append(tbl)
    out.append(Spacer(1, 0.4 * cm))

    # Qualitative cards
    out.append(Paragraph("More wins", styles["h2"]))
    out.append(Spacer(1, 0.1 * cm))
    half = (usable - 0.4 * cm) / 2
    card_cells = []
    for name, text in SUCCESS_CARDS:
        cell = Table(
            [[Paragraph(name, styles["card_name"])],
             [Paragraph(text, styles["card_body"])]],
            colWidths=[half],
        )
        cell.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(C_LIGHT)),
            ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(C_ACCENT)),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ]))
        card_cells.append(cell)
    cards = Table([card_cells], colWidths=[half, half])
    cards.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    out.append(cards)
    return out


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
def _make_footer(start_date: str = START_DATE, end_date: str = END_DATE):
    """Build a page-footer callback that stamps the period + page number."""
    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor(C_MUTED))
        canvas.drawString(doc.leftMargin, 1.1 * cm,
                          f"Z.ai Usage Report · {start_date} to {end_date}")
        canvas.drawRightString(A4[0] - doc.rightMargin, 1.1 * cm,
                               f"Page {doc.page}")
        canvas.restoreState()
    return _footer


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def generate_management_report_pdf(
    df: pd.DataFrame,
    start_date: str,
    end_date: str,
    package_cost: float = PACKAGE_COST,
) -> bytes:
    """Build the management report PDF for the given data and return as bytes.

    Args:
        df: Usage DataFrame with columns billing_date, model_code, token_usage,
            requests, cost, username (already filtered to the period).
        start_date: Period start (YYYY-MM-DD) — used in title/footer text.
        end_date: Period end (YYYY-MM-DD) — used in title/footer text.
        package_cost: Flat subscription cost — drives the ROI multiplier.

    Returns:
        PDF file content as bytes.
    """
    if df.empty:
        raise ValueError(
            f"No usage data found between {start_date} and {end_date}.")

    m = compute_metrics(df, package_cost=package_cost)
    styles = _build_styles()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.4 * cm, rightMargin=1.4 * cm,
        topMargin=1.3 * cm, bottomMargin=1.6 * cm,
        title="Z.ai Usage & Cost-Effectiveness Report",
    )
    usable = A4[0] - doc.leftMargin - doc.rightMargin

    story: list = []
    story += page1_cover(m, styles, usable, start_date, end_date, package_cost)
    story += page3_success(styles, usable)
    story.append(PageBreak())
    story += page2_charts(df, m, styles, usable)

    footer = _make_footer(start_date, end_date)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def main() -> None:
    df = load_usage()
    if df.empty:
        raise SystemExit(f"No usage data found between {START_DATE} and {END_DATE}.")

    pdf_bytes = generate_management_report_pdf(df, START_DATE, END_DATE, PACKAGE_COST)
    OUTPUT_PATH.write_bytes(pdf_bytes)

    m = compute_metrics(df, package_cost=PACKAGE_COST)
    print(f"Wrote {OUTPUT_PATH}")
    print(f"  {m['roi_mult']:.1f}x ROI on ${PACKAGE_COST:.0f} "
          f"(${m['list_value']:,.2f} value)")
    print(f"  {m['total_requests']:,} requests · {fmt_tokens(m['total_tokens'])} tokens "
          f"· {m['active_users']} active engineers")


if __name__ == "__main__":
    main()
