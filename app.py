"""Z.ai Billing Dashboard — Streamlit entrypoint.

Run with: streamlit run app.py
"""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from db import (
    get_distinct_models,
    get_distinct_usernames,
    get_filtered_data,
)
from export_cost import generate_cost_excel
from export_pdf import generate_summary_pdf
from export_user import generate_user_pdf
from zai_sync import sync

st.set_page_config(page_title="Z.ai Usage Monitor", layout="wide")
st.title("Z.ai Usage Monitor")

# ---------------------------------------------------------------------------
# Sidebar — Sync section
# ---------------------------------------------------------------------------
st.sidebar.header("Sync")

sync_range = st.sidebar.date_input(
    "Sync date range",
    value=(date.today().replace(day=1), date.today()),
    key="sync_range",
)
sync_start = sync_range[0] if isinstance(sync_range, (tuple, list)) else sync_range
sync_end = sync_range[1] if isinstance(sync_range, (tuple, list)) and len(sync_range) > 1 else sync_start

if st.sidebar.button("Sync now", type="primary"):
    try:
        customer_id = st.secrets["zai"]["customer_id"]
        token = st.secrets["zai"]["bearer_token"]
    except (KeyError, FileNotFoundError):
        st.sidebar.error(
            "Missing secrets. Add customer_id and bearer_token to "
            ".streamlit/secrets.toml under [zai]."
        )
        st.stop()

    with st.sidebar.spinner("Syncing..."):
        summary = sync(sync_start, sync_end, customer_id, token)

    st.sidebar.success(
        f"Months: {summary['months_processed']}  \n"
        f"Fetched: {summary['fetched']}  \n"
        f"New rows: {summary['inserted']}  \n"
        f"Skipped (out of range): {summary['skipped_out_of_range']}  \n"
        f"Failed pages: {summary['failed_pages']}"
    )
    st.rerun()

# ---------------------------------------------------------------------------
# Sidebar — Filter section
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")

filter_range = st.sidebar.date_input(
    "Date range",
    value=(date.today() - timedelta(days=30), date.today()),
    key="filter_range",
)
filter_start = filter_range[0] if isinstance(filter_range, (tuple, list)) else filter_range
filter_end = filter_range[1] if isinstance(filter_range, (tuple, list)) and len(filter_range) > 1 else filter_start

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
    index=0,
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

# ---------------------------------------------------------------------------
# Sidebar — Export section
# ---------------------------------------------------------------------------
st.sidebar.header("Export Reports")

if st.sidebar.button("Export Summary PDF"):
    pdf_bytes = generate_summary_pdf(df, filter_start, filter_end, group_by)
    st.sidebar.download_button(
        label="Download Summary PDF",
        data=pdf_bytes,
        file_name=f"zai_summary_{filter_start}_{filter_end}.pdf",
        mime="application/pdf",
        key="dl_summary_pdf",
    )

if st.sidebar.button("Export Per-User Report"):
    user_pdf = generate_user_pdf(df, filter_start, filter_end)
    st.sidebar.download_button(
        label="Download Per-User Report",
        data=user_pdf,
        file_name=f"zai_users_{filter_start}_{filter_end}.pdf",
        mime="application/pdf",
        key="dl_user_pdf",
    )

if st.sidebar.button("Export Cost Allocation XLSX"):
    cost_xlsx = generate_cost_excel(df, filter_start, filter_end)
    st.sidebar.download_button(
        label="Download Cost Allocation",
        data=cost_xlsx,
        file_name=f"zai_cost_{filter_start}_{filter_end}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_cost_xlsx",
    )

col1, col2, col3 = st.columns(3)
col1.metric("Total Tokens", f"{int(df['token_usage'].sum()):,}")
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
fig_tokens = px.bar(
    token_agg,
    x=group_col,
    y="token_usage",
    color="token_type",
    title="Tokens",
    barmode="stack",
    color_discrete_map={"INPUT": "#636EFA", "OUTPUT": "#EF553B", "CACHE": "#00CC96"},
)
fig_tokens.update_layout(xaxis_title=group_by, yaxis_title="Tokens")
st.plotly_chart(fig_tokens, use_container_width=True)

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
st.plotly_chart(fig_req, use_container_width=True)

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
st.plotly_chart(fig_cost, use_container_width=True)

# ---------------------------------------------------------------------------
# Raw data (expandable)
# ---------------------------------------------------------------------------
with st.expander("Raw Data"):
    display_cols = [
        "billing_date", "username", "model_code", "token_type",
        "token_usage", "cost_price", "requests", "api_key", "billing_no",
    ]
    st.dataframe(df[display_cols].sort_values("billing_date"), use_container_width=True)
