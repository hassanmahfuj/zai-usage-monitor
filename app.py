"""Z.ai Billing Dashboard — Streamlit entrypoint.

Run with: streamlit run app.py
"""

import calendar
import math
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from db import (
    get_distinct_models,
    get_distinct_usernames,
    get_filtered_data,
    get_contributions_data,
)
from export_cost import generate_cost_excel
from export_report import generate_report_pdf
from export_slides import generate_renewal_slides
from generate_management_report import generate_management_report_pdf
from formatting import fmt_tokens
from zai_sync import sync
from contrib_sync import sync_contributions


def shift_month(d: date, delta: int) -> date:
    """Return the first day of the month offset by `delta` months from `d`."""
    idx = d.month - 1 + delta
    year = d.year + idx // 12
    month = idx % 12 + 1
    return date(year, month, 1)


def month_bounds(d: date) -> tuple[date, date]:
    """Return (first_day, last_day) of the calendar month containing `d`."""
    first = d.replace(day=1)
    last_day = calendar.monthrange(d.year, d.month)[1]
    return first, d.replace(day=last_day)


def shift_range_key(key: str, delta: int) -> None:
    """Callback: shift the month of the date range stored in st.session_state[key].

    Runs on button click before the widget re-instantiates, so it may safely
    overwrite the widget's own key.
    """
    current = st.session_state.get(key)
    ref = current[0] if isinstance(current, (tuple, list)) else current
    if ref is None:
        ref = date.today().replace(day=1)
    st.session_state[key] = month_bounds(shift_month(ref, delta))


def token_axis(max_val: float, n_ticks: int = 5) -> dict:
    """Return Plotly yaxis tickvals/ticktext kwargs formatting tokens as K/M/B."""
    max_val = float(max_val or 0)
    if max_val <= 0:
        return {}
    raw = max_val / n_ticks
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    norm = raw / mag
    if norm < 1.5:
        step = mag
    elif norm < 3:
        step = 2 * mag
    elif norm < 7:
        step = 5 * mag
    else:
        step = 10 * mag
    vals = list(range(0, int(max_val) + step, step))
    return dict(tickvals=vals, ticktext=[fmt_tokens(v) for v in vals])


st.set_page_config(page_title="Z.ai Usage Monitor", layout="wide")
st.title("Z.ai Usage Monitor")
date_range_display = st.empty()

# ---------------------------------------------------------------------------
# Sidebar — Sync section
# ---------------------------------------------------------------------------
st.sidebar.header("Sync")

with st.sidebar.expander("API Usage & Contributions Sync"):
    if "sync_range" not in st.session_state:
        st.session_state["sync_range"] = (date.today().replace(day=1), date.today())
    sync_range = st.date_input("Date range", key="sync_range")
    sync_start = sync_range[0] if isinstance(sync_range, (tuple, list)) else sync_range
    sync_end = sync_range[1] if isinstance(sync_range, (tuple, list)) and len(sync_range) > 1 else sync_start

    _sync_prev, _sync_next = st.columns(2)
    _sync_prev.button("Prev month", key="sync_prev_month", on_click=shift_range_key, args=("sync_range", -1))
    _sync_next.button("Next month", key="sync_next_month", on_click=shift_range_key, args=("sync_range", 1))

    if st.button("Sync API Usage", type="primary"):
        try:
            customer_id = st.secrets["zai"]["customer_id"]
            token = st.secrets["zai"]["api_key"]
        except (KeyError, FileNotFoundError):
            st.error(
                "Missing secrets. Add customer_id and api_key to "
                ".streamlit/secrets.toml under [zai]."
            )
            st.stop()

        with st.spinner("Syncing..."):
            summary = sync(sync_start, sync_end, customer_id, token)

        st.success(
            f"Months: {summary['months_processed']}  \n"
            f"Fetched: {summary['fetched']}  \n"
            f"New rows: {summary['inserted']}  \n"
            f"Skipped (out of range): {summary['skipped_out_of_range']}  \n"
            f"Failed pages: {summary['failed_pages']}"
        )
        st.rerun()

    if st.button("Sync Contributions", type="secondary"):
        try:
            gl = st.secrets["gitlab"]
            gl_url = gl["base_url"]
            gl_user = gl["admin_username"]
            gl_pass = gl["admin_password"]
        except (KeyError, FileNotFoundError):
            st.error(
                "Missing secrets. Add [gitlab] with base_url, admin_username, "
                "and admin_password to .streamlit/secrets.toml."
            )
            st.stop()

        with st.spinner("Syncing contributions..."):
            csummary = sync_contributions(
                sync_start, sync_end, gl_url, gl_user, gl_pass
            )

        if csummary.get("failed_users") == -1:
            st.error("GitLab login failed. Check [gitlab] credentials.")
            st.stop()

        st.success(
            f"Users processed: {csummary['users_processed']}  \n"
            f"Date rows upserted: {csummary['dates_upserted']}  \n"
            f"Skipped (no calendar): {csummary['skipped_no_calendar']}  \n"
            f"Failed users: {csummary['failed_users']}"
        )
        st.rerun()

# ---------------------------------------------------------------------------
# Sidebar — Filter section
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")

if "filter_range" not in st.session_state:
    st.session_state["filter_range"] = (date.today().replace(day=1), date.today())
filter_range = st.sidebar.date_input("Date range", key="filter_range")
filter_start = filter_range[0] if isinstance(filter_range, (tuple, list)) else filter_range
filter_end = filter_range[1] if isinstance(filter_range, (tuple, list)) and len(filter_range) > 1 else filter_start

date_range_display.caption(f"Showing {filter_start} to {filter_end}")

_filter_prev, _filter_next = st.sidebar.columns(2)
_filter_prev.button("Prev month", key="filter_prev_month", on_click=shift_range_key, args=("filter_range", -1))
_filter_next.button("Next month", key="filter_next_month", on_click=shift_range_key, args=("filter_range", 1))

all_models = get_distinct_models()
selected_models = st.sidebar.multiselect(
    "Models",
    options=all_models,
    default=all_models,
)

all_usernames = get_distinct_usernames()
selected_usernames = st.sidebar.multiselect(
    "Users",
    options=all_usernames,
    default=all_usernames,
)

group_by = st.sidebar.radio(
    "Group by",
    options=["Date", "Model", "Username"],
    index=2,
)

# ---------------------------------------------------------------------------
# Main area — KPI cards
# ---------------------------------------------------------------------------
df = get_filtered_data(
    start_date=str(filter_start),
    end_date=str(filter_end),
    models=selected_models if selected_models else None,
    usernames=selected_usernames if selected_usernames else None,
)

if df.empty:
    st.info("No data for the selected filters. Try syncing first.")
    st.stop()

# Contributions data — shares the same date range and username filters.
# Fetched separately from billing (separate table/source). May be empty if
# the user has not run "Sync Contributions" yet.
cdf = get_contributions_data(
    start_date=str(filter_start),
    end_date=str(filter_end),
    usernames=selected_usernames if selected_usernames else None,
)

# ---------------------------------------------------------------------------
# Sidebar — Export section
# ---------------------------------------------------------------------------
st.sidebar.header("Export Reports")

if st.sidebar.button("Export Report"):
    pdf_bytes = generate_report_pdf(df, filter_start, filter_end, cdf)
    st.sidebar.download_button(
        label="Download Report (PDF)",
        data=pdf_bytes,
        file_name=f"zai_report_{filter_start}_{filter_end}.pdf",
        mime="application/pdf",
        key="dl_report_pdf",
    )

if st.sidebar.button("Export Cost Allocation XLSX"):
    cost_xlsx = generate_cost_excel(df, filter_start, filter_end, cdf)
    st.sidebar.download_button(
        label="Download Cost Allocation",
        data=cost_xlsx,
        file_name=f"zai_cost_{filter_start}_{filter_end}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_cost_xlsx",
    )

# Renewal deck — for the "convince the manager" business case. Set the filter
# to the full package period (e.g. Feb–Jun) before exporting so totals reflect
# the whole package window. Package cost drives the ROI multiplier.
st.sidebar.subheader("Renewal Deck")
try:
    _default_pkg = float(st.secrets.get("renewal", {}).get("package_cost", 80.0))
except (TypeError, ValueError):
    _default_pkg = 80.0
package_cost = st.sidebar.number_input(
    "Package cost (USD)",
    min_value=0.0,
    value=_default_pkg,
    step=10.0,
    help="Flat price paid for the Z.ai package. Drives the ROI multiplier "
         "and per-contribution cost on the renewal slides.",
)
if st.sidebar.button("Generate Renewal Slides", type="primary"):
    with st.spinner("Building renewal deck..."):
        html_bytes, zip_bytes = generate_renewal_slides(
            df, filter_start, filter_end, cdf,
            package_cost=package_cost,
        )
    st.session_state["_renewal_html"] = html_bytes
    st.session_state["_renewal_zip"] = zip_bytes
    st.session_state["_renewal_period"] = (filter_start, filter_end)

if "_renewal_html" in st.session_state:
    _p = st.session_state["_renewal_period"]
    st.sidebar.download_button(
        label="Download Renewal Deck (HTML)",
        data=st.session_state["_renewal_html"],
        file_name=f"zai_renewal_{_p[0]}_{_p[1]}.html",
        mime="text/html",
        key="dl_renewal_html",
        help="Self-contained slide deck — emailable, printable (Ctrl/⌘+P).",
    )
    st.sidebar.download_button(
        label="Download Charts (PNG ZIP)",
        data=st.session_state["_renewal_zip"],
        file_name=f"zai_renewal_{_p[0]}_{_p[1]}_charts.zip",
        mime="application/zip",
        key="dl_renewal_zip",
        help="Individual chart PNGs — drop into Google Slides / PowerPoint.",
    )

# Management report — the 2-page manager-facing PDF (cover + success stories
# + charts). Shares the package_cost above; set the filter to the full package
# period (e.g. Feb–Jun) before generating.
if st.sidebar.button("Generate Management Report PDF"):
    with st.spinner("Building management report..."):
        _mgmt_pdf = generate_management_report_pdf(
            df, str(filter_start), str(filter_end), package_cost,
        )
    st.session_state["_mgmt_pdf"] = _mgmt_pdf
    st.session_state["_mgmt_period"] = (filter_start, filter_end)

if "_mgmt_pdf" in st.session_state:
    _mp = st.session_state["_mgmt_period"]
    st.sidebar.download_button(
        label="Download Management Report (PDF)",
        data=st.session_state["_mgmt_pdf"],
        file_name=f"zai_management_report_{_mp[0]}_{_mp[1]}.pdf",
        mime="application/pdf",
        key="dl_mgmt_pdf",
        help="2-page manager PDF — cover, success stories, usage charts.",
    )

col1, col2, col3 = st.columns(3)
col1.metric("Total Tokens", fmt_tokens(df['token_usage'].sum()))
col2.metric("Total Requests", f"{int(df['requests'].sum()):,}")
col3.metric("Total Cost", f"${df['cost'].sum():,.4f}")

# ---------------------------------------------------------------------------
# Main area — Charts
# ---------------------------------------------------------------------------
group_map = {
    "Date": "billing_date",
    "Model": "model_code",
    "Username": "username",
}
group_col = group_map[group_by]

# Token usage — stacked bar by token_type
token_agg = (
    df.groupby([group_col, "token_type"], as_index=False)["token_usage"]
    .sum()
)
token_agg["tokens_display"] = token_agg["token_usage"].apply(fmt_tokens)
fig_tokens = px.bar(
    token_agg,
    x=group_col,
    y="token_usage",
    color="token_type",
    title="Tokens",
    barmode="stack",
    color_discrete_map={"INPUT": "#636EFA", "OUTPUT": "#EF553B", "CACHE": "#00CC96"},
    custom_data=["tokens_display"],
)
_stack_max = token_agg.groupby(group_col)["token_usage"].sum().max()
fig_tokens.update_layout(xaxis_title=group_by, yaxis_title="Tokens")
fig_tokens.update_traces(hovertemplate=f"{group_by}=%{{x}}<br>Tokens=%{{customdata[0]}}<extra>%{{fullData.name}}</extra>")
fig_tokens.update_yaxes(**token_axis(_stack_max))
st.plotly_chart(fig_tokens, width="stretch")

# Requests — bar
req_agg = df.groupby(group_col, as_index=False)["requests"].sum()
fig_req = px.bar(
    req_agg,
    x=group_col,
    y="requests",
    title="Requests",
    color_discrete_sequence=["#FFA15A"],
)
fig_req.update_layout(xaxis_title=group_by, yaxis_title="Requests")
st.plotly_chart(fig_req, width="stretch")

# Cost — bar
cost_agg = df.groupby(group_col, as_index=False)["cost"].sum()
fig_cost = px.bar(
    cost_agg,
    x=group_col,
    y="cost",
    title="Costs",
    color_discrete_sequence=["#AB63FA"],
)
fig_cost.update_layout(xaxis_title=group_by, yaxis_title="Cost ($)")
st.plotly_chart(fig_cost, width="stretch")

# ---------------------------------------------------------------------------
# Tokens vs Contributions — per-user comparison (actual values)
# ---------------------------------------------------------------------------
if not cdf.empty:
    tokens_per_user = (
        df.groupby("username", as_index=False)["token_usage"].sum()
    )
    contrib_per_user = (
        cdf.groupby("username", as_index=False)["count"].sum()
    )
    merged = tokens_per_user.merge(contrib_per_user, on="username", how="outer")
    merged = merged.fillna(0).sort_values("token_usage", ascending=False)
    merged["tokens_display"] = merged["token_usage"].apply(fmt_tokens)

    # Dual y-axis, grouped bars: Tokens (left) vs Contributions (right)
    fig_real = go.Figure()
    fig_real.add_trace(go.Bar(
        x=merged["username"],
        y=merged["token_usage"],
        name="Tokens",
        yaxis="y",
        offsetgroup=0,
        marker_color="#636EFA",
        customdata=merged[["tokens_display"]],
        hovertemplate="Username=%{x}<br>Tokens=%{customdata[0]}<extra>%{fullData.name}</extra>",
    ))
    fig_real.add_trace(go.Bar(
        x=merged["username"],
        y=merged["count"],
        name="Contributions",
        yaxis="y2",
        offsetgroup=1,
        marker_color="#19A3A3",
    ))
    fig_real.update_layout(
        title="Tokens vs Contributions",
        barmode="group",
        xaxis=dict(title="Username", tickangle=-45),
        yaxis=dict(title="Tokens"),
        yaxis2=dict(title="Contributions", overlaying="y", side="right"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    fig_real.update_yaxes(**token_axis(merged["token_usage"].max()))
    st.plotly_chart(fig_real, width="stretch")
else:
    st.info(
        "No contribution data for the selected range. "
        "Click **Sync Contributions** in the sidebar."
    )

# ---------------------------------------------------------------------------
# Combined Leaderboard — per-user billing vs contributions
# ---------------------------------------------------------------------------
bill_per_user = (
    df.groupby("username", as_index=False)
    .agg(
        Tokens=("token_usage", "sum"),
        Requests=("requests", "sum"),
        Cost=("cost", "sum"),
    )
)

if not cdf.empty:
    contrib_per_user = (
        cdf.groupby("username", as_index=False)
        .agg(Contributions=("count", "sum"))
    )
    leaderboard = bill_per_user.merge(contrib_per_user, on="username", how="outer")
else:
    leaderboard = bill_per_user.copy()
    leaderboard["Contributions"] = 0

leaderboard = leaderboard.fillna(0)
leaderboard["Cost"] = leaderboard["Cost"].round(4)

st.subheader("User Leaderboard")
rank_by = st.selectbox(
    "Rank by",
    options=["Tokens", "Requests", "Cost", "Contributions"],
    index=0,
)
leaderboard = leaderboard.sort_values(rank_by, ascending=False).reset_index(drop=True)
leaderboard.insert(0, "#", range(1, len(leaderboard) + 1))
leaderboard["Tokens"] = leaderboard["Tokens"].apply(fmt_tokens)

st.dataframe(
    leaderboard,
    width="stretch",
    hide_index=True,
    column_config={"Tokens": st.column_config.TextColumn(alignment="right")},
)

# ---------------------------------------------------------------------------
# Raw data (expandable)
# ---------------------------------------------------------------------------
with st.expander("Raw Data"):
    display_cols = [
        "billing_date", "username", "model_code", "token_type",
        "token_usage", "cost_price", "requests", "api_key", "billing_no",
    ]
    raw_display = df[display_cols].sort_values("billing_date").copy()
    raw_display["token_usage"] = raw_display["token_usage"].apply(fmt_tokens)
    st.dataframe(raw_display, width="stretch")
