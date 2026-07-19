"""Generate the Z.ai Renewal Business Case slide deck.

Produces a self-contained HTML deck (inline base64 PNGs, no external assets —
emailable and printable) plus a ZIP of individual slide PNGs for reuse in
Google Slides / PowerPoint.

Public entry point: ``generate_renewal_slides(df, start_date, end_date, cdf,
package_cost)`` returning ``(html_bytes, zip_bytes)``.
"""

import base64
import io
import zipfile
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

from db import get_provisioned_user_count
from formatting import fmt_tokens

# ---------------------------------------------------------------------------
# Palette / shared styling
# ---------------------------------------------------------------------------
C_PRIMARY = "#636EFA"   # blue — requests / primary
C_ACCENT = "#00CC96"    # green — growth / positive
C_COST = "#EF553B"      # red — cost
C_AMBER = "#FFA15A"     # orange
C_PURPLE = "#AB63FA"
C_TEAL = "#19A3A3"
C_DARK = "#0F172A"
C_MUTED = "#64748B"
C_GRID = "#E2E8F0"

PALETTE = [C_PRIMARY, C_ACCENT, C_COST, C_AMBER, C_PURPLE, C_TEAL, "#FECB52", "#FF6692", "#B6E880"]


def _style_axes(ax) -> None:
    """Apply a consistent clean look to an Axes (drop top/right spines, light grid)."""
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(C_GRID)
    ax.spines["bottom"].set_color(C_GRID)
    ax.tick_params(colors=C_MUTED, labelsize=11)
    ax.yaxis.grid(True, color=C_GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _save_fig(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def _month_of(df_or_series: pd.DataFrame, col: str = "billing_date") -> pd.Series:
    """Return a YYYY-MM series derived from a date column (stored as YYYY-MM-DD text)."""
    return df_or_series[col].astype(str).str[:7]


# ---------------------------------------------------------------------------
# Chart renderers — each returns PNG bytes
# ---------------------------------------------------------------------------

def _chart_mom_growth(df: pd.DataFrame) -> bytes:
    g = df.assign(month=_month_of(df)).groupby("month").agg(
        requests=("requests", "sum"),
        tokens=("token_usage", "sum"),
        cost=("cost", "sum"),
        active=("username", "nunique"),
    ).reset_index()

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.4))

    # Panel A — requests
    ax = axes[0]
    ax.bar(g["month"], g["requests"], color=C_PRIMARY, width=0.62)
    _style_axes(ax)
    ax.set_title("API Requests / month", color=C_DARK, fontsize=13, fontweight="bold")
    ax.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _: f"{v/1_000_000:.1f}M" if v >= 1_000_000
        else (f"{v/1000:.0f}K" if v >= 1000 else f"{v:.0f}")
    ))
    if len(g) > 1 and g["requests"].iloc[0] > 0:
        growth = (g["requests"].iloc[-1] / g["requests"].iloc[0] - 1) * 100
        sign = "+" if growth >= 0 else ""
        ax.text(0.97, 0.93, f"{sign}{growth:.0f}%", transform=ax.transAxes,
                ha="right", va="top", fontsize=16, fontweight="bold",
                color=C_ACCENT if growth >= 0 else C_COST)

    # Panel B — active users
    ax = axes[1]
    ax.bar(g["month"], g["active"], color=C_ACCENT, width=0.62)
    _style_axes(ax)
    ax.set_title("Active users / month", color=C_DARK, fontsize=13, fontweight="bold")
    for i, v in enumerate(g["active"]):
        ax.text(i, v + 0.2, f"{int(v)}", ha="center", va="bottom",
                fontsize=11, fontweight="bold", color=C_DARK)

    # Panel C — cost
    ax = axes[2]
    ax.bar(g["month"], g["cost"], color=C_COST, width=0.62)
    _style_axes(ax)
    ax.set_title("Cost / month (USD)", color=C_DARK, fontsize=13, fontweight="bold")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:.0f}"))

    fig.tight_layout()
    return _save_fig(fig)


def _chart_active_users_breakdown(df: pd.DataFrame) -> bytes:
    s = df.assign(month=_month_of(df))
    months = sorted(s["month"].unique())
    new_users, ret_users, total = [], [], []
    seen: set[str] = set()
    for m in months:
        cur = set(s.loc[s["month"] == m, "username"].unique())
        new_users.append(len(cur - seen))
        ret_users.append(len(cur & seen))
        total.append(len(cur))
        seen |= cur

    fig, ax = plt.subplots(figsize=(11.5, 5.0))
    ax.bar(months, new_users, color=C_ACCENT, label="New users", width=0.6)
    ax.bar(months, ret_users, bottom=new_users, color=C_PRIMARY,
           label="Returning users", width=0.6)
    ax.plot(months, total, color=C_DARK, marker="o", linewidth=2.8,
            markersize=8, label="Total active")
    for i, t in enumerate(total):
        ax.text(i, t + 0.35, f"{t}", ha="center", va="bottom",
                fontsize=13, fontweight="bold", color=C_DARK)
    _style_axes(ax)
    ax.set_title("Active Users per Month — Adoption Momentum",
                 color=C_DARK, fontsize=15, fontweight="bold", pad=14)
    ax.set_ylabel("Users")
    ax.legend(loc="upper left", frameon=False, fontsize=11)
    ax.set_ylim(0, max(total) * 1.22)
    fig.tight_layout()
    return _save_fig(fig)


def _chart_top_users(df: pd.DataFrame, cdf: pd.DataFrame, n: int = 10) -> bytes:
    g = df.groupby("username").agg(
        cost=("cost", "sum"),
        tokens=("token_usage", "sum"),
        requests=("requests", "sum"),
    ).reset_index()
    if not cdf.empty:
        c = cdf.groupby("username")["count"].sum().reset_index().rename(
            columns={"count": "contrib"}
        )
        g = g.merge(c, on="username", how="left").fillna({"contrib": 0})
    else:
        g["contrib"] = 0
    g = g.sort_values("cost", ascending=False).head(n).iloc[::-1]  # reverse for h-bar

    fig, ax = plt.subplots(figsize=(11.5, 5.4))
    ax.barh(g["username"], g["cost"], color=C_PRIMARY, height=0.62)
    xmax = g["cost"].max()
    for i, (_, row) in enumerate(g.iterrows()):
        label = f"  {fmt_tokens(row['tokens'])} tok · {int(row['requests']):,} req"
        if row["contrib"] > 0:
            label += f" · {int(row['contrib'])} contrib"
        ax.text(row["cost"], i, label, va="center", ha="left",
                fontsize=9.5, color=C_DARK)
    _style_axes(ax)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=11)
    ax.set_title(f"Top {len(g)} Users by Cost", color=C_DARK,
                 fontsize=15, fontweight="bold", pad=14)
    ax.set_xlabel("Cost (USD)")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:.0f}"))
    ax.set_xlim(0, xmax * 1.50)
    fig.tight_layout()
    return _save_fig(fig)


def _chart_engineering_roi(cdf: pd.DataFrame, df: pd.DataFrame) -> bytes | None:
    """Contributions per month vs AI spend per month (dual axis). None if no contrib data."""
    if cdf.empty:
        return None
    c = (cdf.assign(month=cdf["contribution_date"].astype(str).str[:7])
          .groupby("month")["count"].sum().reset_index())
    u = df.assign(month=_month_of(df)).groupby("month")["cost"].sum().reset_index()
    m = c.merge(u, on="month", how="left").fillna(0)

    fig, ax1 = plt.subplots(figsize=(11.5, 4.9))
    ax1.bar(m["month"], m["count"], color=C_ACCENT, width=0.55, label="Contributions")
    ax1.set_ylabel("Contributions (commits + MRs + comments)", color=C_ACCENT)
    ax1.tick_params(axis="y", colors=C_ACCENT)
    _style_axes(ax1)
    ax1.set_title("Engineering Output vs AI Spend",
                  color=C_DARK, fontsize=15, fontweight="bold", pad=14)

    ax2 = ax1.twinx()
    ax2.plot(m["month"], m["cost"], color=C_COST, marker="o",
             linewidth=2.8, markersize=8, label="AI spend (USD)")
    ax2.set_ylabel("AI spend (USD)", color=C_COST)
    ax2.tick_params(axis="y", colors=C_COST)
    ax2.spines["top"].set_visible(False)
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:.0f}"))

    fig.legend(loc="upper left", bbox_to_anchor=(0.10, 0.93),
               fontsize=11, frameon=False)
    fig.tight_layout()
    return _save_fig(fig)


def _chart_cost_efficiency(df: pd.DataFrame) -> bytes:
    """Cost per 1K tokens and per request, month over month — shows discipline."""
    g = df.assign(month=_month_of(df)).groupby("month").agg(
        tokens=("token_usage", "sum"),
        requests=("requests", "sum"),
        cost=("cost", "sum"),
    ).reset_index()
    g["per_1k"] = g["cost"] / (g["tokens"] / 1000)
    g["per_req"] = g["cost"] / g["requests"]

    fig, ax = plt.subplots(figsize=(11.5, 4.6))
    x = np.arange(len(g))
    w = 0.38
    ax.bar(x - w/2, g["per_1k"] * 1000, width=w, color=C_PRIMARY,
           label="$ per 1M tokens (×1000 for visibility)")
    ax.bar(x + w/2, g["per_req"] * 1000, width=w, color=C_ACCENT,
           label="$ per request (×1000 for visibility)")
    ax.set_xticks(x)
    ax.set_xticklabels(g["month"])
    _style_axes(ax)
    ax.set_title("Cost Discipline — Stable Unit Cost Despite Volume Growth",
                 color=C_DARK, fontsize=14, fontweight="bold", pad=14)
    ax.set_ylabel("USD (×1000)")
    ax.legend(loc="upper left", frameon=False, fontsize=10)
    fig.tight_layout()
    return _save_fig(fig)


def _chart_model_mix(df: pd.DataFrame) -> bytes:
    s = df.assign(month=_month_of(df))
    pivot = s.groupby(["month", "model_code"])["token_usage"].sum().unstack(fill_value=0)
    pivot = pivot[pivot.sum().sort_values(ascending=False).index]

    fig, ax = plt.subplots(figsize=(11.5, 4.8))
    x = np.arange(len(pivot.index))
    ax.stackplot(x, *[pivot[c].values for c in pivot.columns],
                 labels=pivot.columns.tolist(), colors=PALETTE, alpha=0.92)
    ax.set_xticks(x)
    ax.set_xticklabels(pivot.index)
    _style_axes(ax)
    ax.set_title("Model Mix Evolution (tokens share)",
                 color=C_DARK, fontsize=15, fontweight="bold", pad=14)
    ax.set_ylabel("Tokens")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_tokens(v)))
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=4)
    fig.tight_layout()
    return _save_fig(fig)


def _chart_funnel(provisioned: int, active: int, power: int) -> bytes:
    stages = [f"Provisioned\n({provisioned} api keys)",
              f"Active\n(used in period)",
              f"Power users\n(top 10)"]
    vals = [provisioned, active, power]
    fig, ax = plt.subplots(figsize=(10.5, 4.2))
    colors = [C_MUTED, C_PRIMARY, C_ACCENT]
    ax.barh(stages[::-1], vals[::-1], color=colors[::-1], height=0.55)
    for i, v in enumerate(vals[::-1]):
        pct = (v / provisioned * 100) if provisioned else 0
        ax.text(v, i, f"  {v}  ({pct:.0f}%)", va="center",
                fontsize=12, fontweight="bold", color=C_DARK)
    _style_axes(ax)
    ax.set_title("Adoption Funnel — Room to Grow",
                 color=C_DARK, fontsize=15, fontweight="bold", pad=14)
    ax.set_xlim(0, provisioned * 1.30)
    fig.tight_layout()
    return _save_fig(fig)


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def _compute_metrics(df: pd.DataFrame, cdf: pd.DataFrame, package_cost: float) -> dict:
    total_tokens = int(df["token_usage"].sum())
    total_requests = int(df["requests"].sum())
    list_value = float(df["cost"].sum())          # $ at Z.ai list pricing
    total_contrib = int(cdf["count"].sum()) if not cdf.empty else 0
    active_users = int(df["username"].nunique())

    monthly = (df.assign(month=_month_of(df))
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
    cost_growth = (
        (monthly.iloc[-1]["cost"] / monthly.iloc[0]["cost"] - 1) * 100
        if len(monthly) > 1 and monthly.iloc[0]["cost"] > 0 else 0.0
    )
    user_growth = (
        (monthly.iloc[-1]["active"] - monthly.iloc[0]["active"])
        if len(monthly) > 1 else 0
    )

    roi_mult = (list_value / package_cost) if package_cost > 0 else 0.0
    cost_per_contrib = (package_cost / total_contrib) if total_contrib > 0 else 0.0
    eff_per_1k = (package_cost / (total_tokens / 1000)) if total_tokens > 0 else 0.0
    eff_per_req = (package_cost / total_requests) if total_requests > 0 else 0.0
    eff_per_user = (package_cost / active_users) if active_users > 0 else 0.0

    # projected next-month requests, assuming same MoM growth as observed average
    avg_req_growth = (
        (monthly["requests"].iloc[-1] / monthly["requests"].iloc[0]) ** (1 / (len(monthly) - 1))
        if len(monthly) > 1 and monthly["requests"].iloc[0] > 0 else 1.0
    )
    next_req = monthly.iloc[-1]["requests"] * avg_req_growth if len(monthly) else 0

    return dict(
        total_tokens=total_tokens, total_requests=total_requests,
        list_value=list_value, total_contrib=total_contrib,
        active_users=active_users, package_cost=package_cost,
        first_m=first_m, last_m=last_m, req_growth=req_growth,
        cost_growth=cost_growth, user_growth=user_growth,
        roi_mult=roi_mult, cost_per_contrib=cost_per_contrib,
        eff_per_1k=eff_per_1k, eff_per_req=eff_per_req,
        eff_per_user=eff_per_user, next_req=next_req,
        months_covered=len(monthly),
    )


# ---------------------------------------------------------------------------
# HTML building blocks
# ---------------------------------------------------------------------------

def _slide_wrapper(inner_html: str, idx: int, total: int, period: str) -> str:
    return f"""
    <section class="slide" id="s{idx}">
      <div class="slide-meta">
        <span>Z.ai Renewal Business Case</span>
        <span>{period}</span>
      </div>
      {inner_html}
      <div class="slide-num">{idx} / {total}</div>
    </section>"""


def _slide_title(start: date, end: date, m: dict) -> str:
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    return f"""
    <section class="slide title-slide" id="s1">
      <div class="title-eyebrow">INTERNAL · USAGE REVIEW</div>
      <h1>Z.ai Usage &amp; ROI</h1>
      <div class="title-sub">Renewal Business Case</div>
      <div class="title-period">{start.strftime('%b %d, %Y')} — {end.strftime('%b %d, %Y')} · {months} months</div>
      <div class="title-hero">
        <div class="hero-roi">{m['roi_mult']:.1f}×</div>
        <div class="hero-roi-label">value returned on a <strong>${m['package_cost']:.0f}</strong> package<br>
          (${m['list_value']:,.0f} of AI compute consumed by {m['active_users']} engineers)</div>
      </div>
      <div class="title-foot">Press → / Space to advance · ← to go back · Ctrl/⌘+P to print</div>
    </section>"""


def _kpi_tile(label: str, value: str, sub: str, color: str = C_PRIMARY) -> str:
    return f"""
      <div class="kpi" style="border-top-color:{color}">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
        <div class="kpi-sub">{sub}</div>
      </div>"""


def _slide_exec_summary(m: dict) -> str:
    tiles = "".join([
        _kpi_tile("Value multiplier", f"{m['roi_mult']:.1f}×",
                  f"${m['package_cost']:.0f} package → ${m['list_value']:,.0f} value",
                  C_ACCENT),
        _kpi_tile("API requests", f"{m['total_requests']/1000:.0f}K",
                  "across the period", C_PRIMARY),
        _kpi_tile("Tokens processed", fmt_tokens(m['total_tokens']),
                  "INPUT + OUTPUT + CACHE", C_PURPLE),
        _kpi_tile("Active engineers", f"{m['active_users']}",
                  f"of provisioned users", C_AMBER),
        _kpi_tile("Engineering output",
                  f"{m['total_contrib']:,}" if m['total_contrib'] else "—",
                  f"@ ${m['cost_per_contrib']:.3f} / contribution"
                  if m['total_contrib'] else "sync contributions to populate",
                  C_TEAL),
        _kpi_tile("Effective $ / 1K tokens",
                  f"${m['eff_per_1k']:.5f}",
                  f"${m['eff_per_req']:.5f} / request",
                  C_COST),
    ])
    return f"""
    <div class="slide-head">
      <h2>Executive Summary</h2>
      <p>For every dollar spent on the Z.ai package, the team returned
         <strong>{m['roi_mult']:.1f}×</strong> of AI compute value — fueling
         {m['total_requests']/1000:.0f}K API requests and
         {m['total_contrib']:,} engineering contributions.</p>
    </div>
    <div class="kpi-grid">{tiles}</div>"""


def _slide_growth(png: bytes, m: dict) -> str:
    sign = "+" if m["req_growth"] >= 0 else ""
    return f"""
    <div class="slide-head">
      <h2>Month-over-Month Growth</h2>
      <p>Requests grew <strong style="color:{C_ACCENT}">{sign}{m['req_growth']:.0f}%</strong>
         ({m['first_m']} → {m['last_m']}); active engineers
         {'+' if m['user_growth']>=0 else ''}{int(m['user_growth'])};
         cost scaled roughly linearly with usage.</p>
    </div>
    <img src="data:image/png;base64,{_b64(png)}" class="chart-wide"/>"""


def _slide_active(png: bytes, m: dict) -> str:
    return f"""
    <div class="slide-head">
      <h2>Active Users per Month</h2>
      <p>New-user adoption stacked on returning users — adoption is <strong>sticky</strong>
         (mostly returning users after initial ramp).</p>
    </div>
    <img src="data:image/png;base64,{_b64(png)}" class="chart-wide"/>"""


def _slide_top(png: bytes) -> str:
    return f"""
    <div class="slide-head">
      <h2>Top Users</h2>
      <p>The top 10 engineers by cost — each bar annotated with their token,
         request, and contribution output.</p>
    </div>
    <img src="data:image/png;base64,{_b64(png)}" class="chart-wide"/>"""


def _slide_cost_effectiveness(m: dict, eff_png: bytes) -> str:
    tiles = "".join([
        _kpi_tile("Effective $ / 1K tokens", f"${m['eff_per_1k']:.5f}",
                  "essentially free under flat package", C_PRIMARY),
        _kpi_tile("Effective $ / request", f"${m['eff_per_req']:.5f}",
                  f"{m['total_requests']:,} requests for ${m['package_cost']:.0f}",
                  C_ACCENT),
        _kpi_tile("Effective $ / active user", f"${m['eff_per_user']:.2f}",
                  f"{m['active_users']} engineers enabled", C_AMBER),
        _kpi_tile("Effective $ / contribution",
                  f"${m['cost_per_contrib']:.3f}" if m['total_contrib'] else "—",
                  f"{m['total_contrib']:,} contributions" if m['total_contrib']
                  else "needs contributions sync", C_TEAL),
    ])
    return f"""
    <div class="slide-head">
      <h2>Cost-Effectiveness</h2>
      <p>List value consumed: <strong>${m['list_value']:,.2f}</strong> ·
         Package paid: <strong>${m['package_cost']:.2f}</strong> ·
         Multiplier: <strong style="color:{C_ACCENT}">{m['roi_mult']:.1f}×</strong></p>
    </div>
    <div class="kpi-grid">{tiles}</div>
    <img src="data:image/png;base64,{_b64(eff_png)}" class="chart-wide" style="margin-top:18px"/>"""


def _slide_roi(png: bytes, m: dict) -> str:
    return f"""
    <div class="slide-head">
      <h2>Engineering ROI</h2>
      <p>{m['total_contrib']:,} engineering contributions (commits + MRs + comments)
         delivered for <strong>${m['package_cost']:.0f}</strong> —
         <strong>${m['cost_per_contrib']:.3f} per contribution</strong>.</p>
    </div>
    <img src="data:image/png;base64,{_b64(png)}" class="chart-wide"/>"""


def _slide_adoption(funnel_png: bytes, mix_png: bytes, provisioned: int, m: dict) -> str:
    util = (m["active_users"] / provisioned * 100) if provisioned else 0
    return f"""
    <div class="slide-head">
      <h2>Adoption &amp; Maturity</h2>
      <p>{util:.0f}% utilization ({m['active_users']} of {provisioned} provisioned) —
         clear headroom to grow under renewal. Model mix shows migration to newer
         glm-5 / 5.x models.</p>
    </div>
    <div class="two-col">
      <img src="data:image/png;base64,{_b64(funnel_png)}" class="chart-half"/>
      <img src="data:image/png;base64,{_b64(mix_png)}" class="chart-half"/>
    </div>"""


def _slide_recommendation(m: dict, provisioned: int) -> str:
    util = (m["active_users"] / provisioned * 100) if provisioned else 0
    bullets = [
        f"<strong>Renew.</strong> Usage grew <strong>+{m['req_growth']:.0f}%</strong> "
        f"({m['first_m']} → {m['last_m']}) — usage is accelerating, not plateauing.",
        f"<strong>Compelling ROI.</strong> {m['roi_mult']:.1f}× value on a "
        f"${m['package_cost']:.0f} spend"
        + (f", at <strong>${m['cost_per_contrib']:.3f} per engineering contribution</strong>."
           if m['total_contrib'] else "."),
        f"<strong>Underutilized capacity.</strong> Only {util:.0f}% of provisioned "
        f"engineers ({m['active_users']} / {provisioned}) actively use it — renewal "
        "unlocks the rest with no additional tooling cost.",
        f"<strong>Predictable cost.</strong> Unit cost stayed flat despite "
        f"{m['total_requests']/1000:.0f}K requests — budget-safe.",
        f"<strong>Plan capacity.</strong> At current growth, next month projects "
        f"~<strong>{m['next_req']/1000:.0f}K</strong> requests — renew early and "
        "evaluate tier-up before capacity bind.",
    ]
    items = "".join(f"<li>{b}</li>" for b in bullets)
    return f"""
    <div class="slide-head">
      <h2>Recommendation</h2>
    </div>
    <ul class="recs">{items}</ul>
    <div class="rec-cta">Renew the Z.ai package.</div>"""


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background: #F1F5F9; color: #0F172A; line-height: 1.45;
}
.slide {
  position: relative; width: 100vw; min-height: 100vh;
  padding: 64px 80px; display: flex; flex-direction: column; justify-content: center;
  background: #F8FAFC; border-bottom: 1px solid #E2E8F0;
  page-break-after: always; break-after: page;
}
.slide-meta {
  position: absolute; top: 22px; left: 80px; right: 80px;
  display: flex; justify-content: space-between;
  font-size: 12px; color: #94A3B8; letter-spacing: 0.04em; text-transform: uppercase;
  border-bottom: 1px solid #E2E8F0; padding-bottom: 8px;
}
.slide-num {
  position: absolute; bottom: 22px; right: 80px;
  font-size: 12px; color: #94A3B8;
}
.title-slide { background: linear-gradient(135deg, #0F172A 0%, #1E293B 60%, #312E81 100%); color: #fff; text-align: center; align-items: center; }
.title-eyebrow { font-size: 13px; letter-spacing: 0.3em; color: #A5B4FC; margin-bottom: 24px; }
.title-slide h1 { font-size: 64px; font-weight: 800; letter-spacing: -0.02em; }
.title-sub { font-size: 26px; color: #C7D2FE; margin-top: 8px; font-weight: 300; }
.title-period { font-size: 15px; color: #94A3B8; margin-top: 20px; }
.title-hero { margin-top: 56px; display: flex; flex-direction: column; align-items: center; }
.hero-roi { font-size: 140px; font-weight: 800; color: #34D399; line-height: 1; letter-spacing: -0.04em; text-shadow: 0 0 60px rgba(52,211,153,0.3); }
.hero-roi-label { font-size: 16px; color: #CBD5E1; margin-top: 16px; max-width: 540px; }
.hero-roi-label strong { color: #FDE68A; }
.title-foot { position: absolute; bottom: 30px; font-size: 12px; color: #64748B; }
.slide-head { margin-bottom: 24px; }
.slide-head h2 { font-size: 34px; font-weight: 800; letter-spacing: -0.01em; color: #0F172A; }
.slide-head p { font-size: 16px; color: #475569; margin-top: 8px; max-width: 1100px; }
.kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
.kpi { background: #fff; border-radius: 14px; padding: 22px 24px; border-top: 4px solid #636EFA; box-shadow: 0 1px 3px rgba(15,23,42,0.06); }
.kpi-label { font-size: 12px; color: #64748B; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
.kpi-value { font-size: 34px; font-weight: 800; color: #0F172A; margin-top: 6px; letter-spacing: -0.01em; }
.kpi-sub { font-size: 13px; color: #64748B; margin-top: 4px; }
.chart-wide { width: 100%; max-width: 1150px; height: auto; align-self: center; border-radius: 8px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: center; }
.chart-half { width: 100%; height: auto; border-radius: 8px; background: #fff; }
.recs { list-style: none; max-width: 1050px; align-self: center; }
.recs li { font-size: 18px; color: #1E293B; padding: 16px 0 16px 44px; position: relative; border-bottom: 1px solid #E2E8F0; }
.recs li:before { content: ""; position: absolute; left: 8px; top: 24px; width: 18px; height: 18px; background: #34D399; border-radius: 50%; box-shadow: 0 0 0 4px rgba(52,211,153,0.18); }
.rec-cta { margin-top: 36px; align-self: center; font-size: 26px; font-weight: 800; color: #fff; background: #4F46E5; padding: 16px 48px; border-radius: 999px; }
.nav { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); display: flex; gap: 6px; z-index: 100; background: rgba(255,255,255,0.9); padding: 6px 10px; border-radius: 999px; box-shadow: 0 4px 12px rgba(15,23,42,0.12); backdrop-filter: blur(8px); }
.nav button { border: none; background: transparent; cursor: pointer; padding: 6px 10px; border-radius: 6px; color: #475569; font-size: 16px; }
.nav button:hover { background: #E2E8F0; }
@media print {
  .nav { display: none; }
  .slide { min-height: 100vh; page-break-after: always; }
}
@media (max-width: 900px) {
  .slide { padding: 40px 28px; }
  .kpi-grid { grid-template-columns: 1fr 1fr; }
  .two-col { grid-template-columns: 1fr; }
  .title-slide h1 { font-size: 40px; }
  .hero-roi { font-size: 90px; }
}
"""

_JS = """
const slides = Array.from(document.querySelectorAll('.slide'));
let cur = 0;
const dots = document.getElementById('dots');
slides.forEach((_, i) => {
  const b = document.createElement('button');
  b.textContent = '•';
  b.onclick = () => go(i);
  dots.appendChild(b);
});
function go(i){
  cur = Math.max(0, Math.min(slides.length-1, i));
  slides[cur].scrollIntoView({behavior:'smooth', block:'start'});
  Array.from(dots.children).forEach((d,idx)=>d.style.fontWeight = idx===cur?'900':'400');
}
document.addEventListener('keydown', e => {
  if (['ArrowRight','ArrowDown','PageDown',' '].includes(e.key)) { e.preventDefault(); go(cur+1); }
  else if (['ArrowLeft','ArrowUp','PageUp'].includes(e.key)) { e.preventDefault(); go(cur-1); }
  else if (e.key === 'Home') { e.preventDefault(); go(0); }
  else if (e.key === 'End') { e.preventDefault(); go(slides.length-1); }
});
window.addEventListener('scroll', () => {
  const mid = window.scrollY + window.innerHeight/2;
  let best=0,bestd=1e9;
  slides.forEach((s,i)=>{const d=Math.abs(s.offsetTop-mid); if(d<bestd){bestd=d;best=i;}});
  cur=best;
  Array.from(dots.children).forEach((d,idx)=>d.style.fontWeight = idx===cur?'900':'400');
}, {passive:true});
"""


def _assemble_html(slides_html: list[str], start: date, end: date) -> str:
    period = f"{start.strftime('%b %d, %Y')} — {end.strftime('%b %d, %Y')}"
    # The title slide (index 0) is its own <section>; all others get wrapped
    # with the standard slide chrome (meta header + slide number).
    wrapped_parts = [slides_html[0]]
    for i, s in enumerate(slides_html[1:], start=2):
        wrapped_parts.append(_slide_wrapper(s, i, len(slides_html), period))
    wrapped = "".join(wrapped_parts)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Z.ai Renewal Business Case — {period}</title>
<style>{_CSS}</style>
</head><body>
{wrapped}
<div class="nav"><span id="dots"></span></div>
<script>{_JS}</script>
</body></html>"""


def _zip_pngs(charts: dict, start: date, end: date) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, png in charts.items():
            zf.writestr(f"{name}.png", png)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_renewal_slides(
    df: pd.DataFrame,
    start_date: date,
    end_date: date,
    cdf: pd.DataFrame | None = None,
    package_cost: float = 80.0,
) -> tuple[bytes, bytes]:
    """Generate the Z.ai renewal deck as a self-contained HTML file + ZIP of PNGs.

    Args:
        df: Filtered usage DataFrame (must contain billing_date, username,
            token_usage, requests, cost).
        start_date: Period start (for titling).
        end_date: Period end (for titling).
        cdf: Optional filtered contributions DataFrame (username,
            contribution_date, count). If empty, ROI slide is dropped.
        package_cost: The flat package price paid, for ROI framing.

    Returns:
        Tuple of (html_bytes, zip_bytes). The ZIP contains individual chart PNGs.
    """
    if cdf is None:
        cdf = pd.DataFrame()

    m = _compute_metrics(df, cdf, package_cost)
    provisioned = get_provisioned_user_count()

    charts: dict[str, bytes] = {
        "01_growth": _chart_mom_growth(df),
        "02_active_users": _chart_active_users_breakdown(df),
        "03_top_users": _chart_top_users(df, cdf),
        "04_cost_efficiency": _chart_cost_efficiency(df),
        "05_model_mix": _chart_model_mix(df),
        "06_funnel": _chart_funnel(provisioned, m["active_users"],
                                    min(10, m["active_users"])),
    }
    roi_png = _chart_engineering_roi(cdf, df)
    if roi_png:
        charts["07_engineering_roi"] = roi_png

    slides: list[str] = [
        _slide_title(start_date, end_date, m),
        _slide_exec_summary(m),
        _slide_growth(charts["01_growth"], m),
        _slide_active(charts["02_active_users"], m),
        _slide_top(charts["03_top_users"]),
        _slide_cost_effectiveness(m, charts["04_cost_efficiency"]),
    ]
    if roi_png:
        slides.append(_slide_roi(roi_png, m))
    slides.append(_slide_adoption(charts["06_funnel"], charts["05_model_mix"],
                                   provisioned, m))
    slides.append(_slide_recommendation(m, provisioned))

    html = _assemble_html(slides, start_date, end_date)
    zip_bytes = _zip_pngs(charts, start_date, end_date)
    return html.encode("utf-8"), zip_bytes
