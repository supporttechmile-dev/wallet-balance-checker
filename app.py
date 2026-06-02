"""Wallet Balance Checker — upload CSV, fetch multi-chain balances, download results."""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from fetch_wallet_balances import (
    CHAINS,
    DEFAULT_CALLS_PER_SECOND,
    EtherscanClient,
    FREE_TIER_CHAINS,
    enrich_rows,
    fetch_balances,
    get_active_chains,
    count_wallets_with_balance,
    load_env_file,
    output_fieldnames,
    read_wallet_csv_content,
    rows_to_csv_bytes,
    unique_addresses,
)

SCRIPT_DIR = Path(__file__).resolve().parent
load_env_file(SCRIPT_DIR / ".env")

st.set_page_config(
    page_title="Wallet Balance Checker",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="expanded",
)

THEMES = {
    "dark": {
        "bg": "#0b0f17",
        "bg_soft": "#111827",
        "card": "rgba(17, 24, 39, 0.82)",
        "card_border": "rgba(99, 102, 241, 0.28)",
        "text": "#f8fafc",
        "muted": "#94a3b8",
        "accent": "#818cf8",
        "accent_2": "#22d3ee",
        "success": "#34d399",
        "warning": "#fbbf24",
        "hero_grad": "linear-gradient(135deg, #312e81 0%, #0f766e 55%, #111827 100%)",
        "shadow": "0 24px 60px rgba(0, 0, 0, 0.45)",
    },
    "light": {
        "bg": "#f4f7fb",
        "bg_soft": "#ffffff",
        "card": "rgba(255, 255, 255, 0.92)",
        "card_border": "rgba(99, 102, 241, 0.18)",
        "text": "#0f172a",
        "muted": "#64748b",
        "accent": "#4f46e5",
        "accent_2": "#0891b2",
        "success": "#059669",
        "warning": "#d97706",
        "hero_grad": "linear-gradient(135deg, #4f46e5 0%, #0d9488 55%, #eef2ff 100%)",
        "shadow": "0 20px 50px rgba(15, 23, 42, 0.12)",
    },
}


def init_session() -> None:
    if "theme" not in st.session_state:
        st.session_state.theme = "dark"
    if "results" not in st.session_state:
        st.session_state.results = None


def get_api_key() -> str:
    """Load API key from Streamlit secrets (cloud) or environment / .env (local)."""
    candidates: list[str] = []

    try:
        candidates.append(str(st.secrets["ETHERSCAN_API_KEY"]).strip())
    except Exception:
        pass

    try:
        value = st.secrets.get("ETHERSCAN_API_KEY", "")
        if value:
            candidates.append(str(value).strip())
    except Exception:
        pass

    env_value = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    if env_value:
        candidates.append(env_value)

    for value in candidates:
        if value and value not in {"your_api_key_here", "your_etherscan_api_key_here"}:
            return value
    return ""


def api_key_setup_hint() -> str:
    runtime = os.environ.get("STREAMLIT_RUNTIME_ENV", "").lower()
    host = os.environ.get("HOST", "").lower()
    if runtime == "cloud" or "streamlit.app" in host:
        return (
            "Add your key in **Streamlit Cloud → Manage app → Settings → Secrets**:\n\n"
            "```toml\nETHERSCAN_API_KEY = \"your_key_here\"\n```"
        )
    return "Add `ETHERSCAN_API_KEY` to your `.env` file in the project folder."


def mask_api_key(key: str) -> str:
    if len(key) <= 8:
        return "••••••••"
    return f"{key[:4]}••••••••{key[-4:]}"


def apply_theme(theme_name: str) -> None:
    theme = THEMES[theme_name]
    st.markdown(
        f"""
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

            html, body, [class*="css"] {{
                font-family: 'Inter', sans-serif;
            }}

            .stApp {{
                background:
                    radial-gradient(circle at top right, rgba(99, 102, 241, 0.18), transparent 28%),
                    radial-gradient(circle at bottom left, rgba(34, 211, 238, 0.12), transparent 24%),
                    {theme["bg"]};
                color: {theme["text"]};
            }}

            [data-testid="stSidebar"] {{
                background: {theme["bg_soft"]};
                border-right: 1px solid {theme["card_border"]};
            }}

            [data-testid="stSidebar"] * {{
                color: {theme["text"]} !important;
            }}

            .hero {{
                background: {theme["hero_grad"]};
                border: 1px solid {theme["card_border"]};
                border-radius: 24px;
                padding: 2rem 2.2rem;
                margin-bottom: 1.5rem;
                box-shadow: {theme["shadow"]};
            }}

            .hero h1 {{
                margin: 0;
                font-size: 2.4rem;
                font-weight: 800;
                letter-spacing: -0.03em;
                color: white;
            }}

            .hero p {{
                margin: 0.75rem 0 0;
                color: rgba(255, 255, 255, 0.88);
                font-size: 1.05rem;
                max-width: 720px;
            }}

            .badge-row {{
                display: flex;
                gap: 0.75rem;
                flex-wrap: wrap;
                margin-top: 1.25rem;
            }}

            .badge {{
                display: inline-flex;
                align-items: center;
                gap: 0.4rem;
                padding: 0.45rem 0.85rem;
                border-radius: 999px;
                background: rgba(255, 255, 255, 0.14);
                border: 1px solid rgba(255, 255, 255, 0.18);
                color: white;
                font-size: 0.82rem;
                font-weight: 600;
            }}

            .panel {{
                background: {theme["card"]};
                border: 1px solid {theme["card_border"]};
                border-radius: 20px;
                padding: 1.35rem 1.5rem;
                box-shadow: {theme["shadow"]};
                margin-bottom: 1rem;
            }}

            .panel-title {{
                font-size: 1rem;
                font-weight: 700;
                margin-bottom: 0.35rem;
                color: {theme["text"]};
            }}

            .panel-copy {{
                color: {theme["muted"]};
                font-size: 0.92rem;
                margin-bottom: 0.8rem;
            }}

            .metric-card {{
                background: {theme["card"]};
                border: 1px solid {theme["card_border"]};
                border-radius: 18px;
                padding: 1.1rem 1.2rem;
                box-shadow: {theme["shadow"]};
                min-height: 118px;
            }}

            .metric-label {{
                color: {theme["muted"]};
                font-size: 0.82rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.06em;
            }}

            .metric-value {{
                color: {theme["text"]};
                font-size: 2rem;
                font-weight: 800;
                margin-top: 0.35rem;
                line-height: 1.1;
            }}

            .metric-accent {{
                color: {theme["accent_2"]};
            }}

            .status-pill {{
                display: inline-block;
                padding: 0.35rem 0.75rem;
                border-radius: 999px;
                font-size: 0.78rem;
                font-weight: 700;
                background: rgba(52, 211, 153, 0.16);
                color: {theme["success"]};
                border: 1px solid rgba(52, 211, 153, 0.28);
            }}

            div[data-testid="stFileUploader"] section {{
                background: {theme["card"]};
                border: 1px dashed {theme["card_border"]};
                border-radius: 18px;
                padding: 0.5rem;
            }}

            div[data-testid="stButton"] > button[kind="primary"] {{
                background: linear-gradient(135deg, {theme["accent"]}, {theme["accent_2"]});
                color: white;
                border: none;
                border-radius: 14px;
                font-weight: 700;
                padding: 0.8rem 1rem;
                box-shadow: 0 12px 30px rgba(79, 70, 229, 0.28);
            }}

            div[data-testid="stButton"] > button[kind="primary"]:hover {{
                filter: brightness(1.05);
            }}

            div[data-testid="stDownloadButton"] > button {{
                border-radius: 14px;
                font-weight: 700;
            }}

            div[data-testid="stVerticalBlock"] > div.scan-btn-wrap div[data-testid="stButton"] > button {{
                background: linear-gradient(135deg, #6366f1 0%, #06b6d4 50%, #10b981 100%);
                color: white;
                border: none;
                border-radius: 16px;
                font-weight: 800;
                font-size: 1.05rem;
                letter-spacing: 0.02em;
                padding: 0.95rem 2rem;
                min-height: 3.25rem;
                box-shadow: 0 16px 40px rgba(6, 182, 212, 0.35);
                transition: transform 0.15s ease, box-shadow 0.15s ease;
            }}

            div.scan-btn-wrap div[data-testid="stButton"] > button:hover {{
                transform: translateY(-1px);
                box-shadow: 0 20px 48px rgba(99, 102, 241, 0.42);
                filter: brightness(1.06);
            }}

            .step-grid {{
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 1rem;
            }}

            .step-card {{
                background: {theme["card"]};
                border: 1px solid {theme["card_border"]};
                border-radius: 18px;
                padding: 1rem;
                min-height: 130px;
            }}

            .step-num {{
                width: 34px;
                height: 34px;
                border-radius: 10px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                background: rgba(99, 102, 241, 0.16);
                color: {theme["accent"]};
                font-weight: 800;
                margin-bottom: 0.75rem;
            }}

            .step-title {{
                font-weight: 700;
                color: {theme["text"]};
                margin-bottom: 0.35rem;
            }}

            .step-copy {{
                color: {theme["muted"]};
                font-size: 0.88rem;
                line-height: 1.45;
            }}

            @media (max-width: 900px) {{
                .step-grid {{
                    grid-template-columns: 1fr 1fr;
                }}
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_metric(label: str, value: str | int, accent: bool = False) -> None:
    value_class = "metric-value metric-accent" if accent else "metric-value"
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="{value_class}">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_step_cards() -> None:
    st.markdown(
        """
        <div class="step-grid">
            <div class="step-card">
                <div class="step-num">1</div>
                <div class="step-title">Upload</div>
                <div class="step-copy">Use your existing wallet export as CSV.</div>
            </div>
            <div class="step-card">
                <div class="step-num">2</div>
                <div class="step-title">Scan</div>
                <div class="step-copy">Balances are fetched across supported chains.</div>
            </div>
            <div class="step-card">
                <div class="step-num">3</div>
                <div class="step-title">Review</div>
                <div class="step-copy">See totals and preview results instantly.</div>
            </div>
            <div class="step-card">
                <div class="step-num">4</div>
                <div class="step-title">Download</div>
                <div class="step-copy">Export the updated sheet with balance columns.</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_limitations() -> None:
    with st.expander("Supported blockchains & limits", expanded=False):
        st.markdown(
            """
            **Etherscan API V2** queries **native coin** balances on EVM chains using one API key.
            It does **not** return ERC-20 token balances (USDT, USDC, etc.) in this app.

            | Network | Native coin | Free API | Column added when scan succeeds |
            |---|---|:---:|---|
            | Ethereum | ETH | Yes | `balance_ethereum` |
            | Arbitrum One | ETH | Yes | `balance_arbitrum` |
            | Polygon | POL | Yes | `balance_polygon` |
            | Gnosis | xDAI | Yes | `balance_gnosis` |
            | Linea | ETH | Yes | `balance_linea` |
            | Blast | ETH | Yes | `balance_blast` |
            | Mantle | MNT | Yes | `balance_mantle` |
            | Optimism | ETH | Paid plan | `balance_optimism` |
            | Base | ETH | Paid plan | `balance_base` |
            | BNB Smart Chain | BNB | Paid plan | `balance_bsc` |
            | Avalanche C-Chain | AVAX | Paid plan | `balance_avalanche` |
            | Scroll | ETH | Not on API V2 | — |
            | zkSync Era | ETH | Not on API V2 | — |

            **Column rules**
            - **`balance_ethereum`** is always included — shows **`0`** or the ETH amount
            - **`multichain_summary`** is always included — shows **`0`** or other-chain balances
            - **`balance_fetch_status`** is always included
            - Extra chain columns appear **only when that wallet has a non-zero balance**

            **API limits (free key):** ~3 req/sec · ~100,000 req/day
            """
        )


init_session()

with st.sidebar:
    st.markdown("### Appearance")
    dark_mode = st.toggle("Dark mode", value=st.session_state.theme == "dark")
    new_theme = "dark" if dark_mode else "light"
    if new_theme != st.session_state.theme:
        st.session_state.theme = new_theme
        st.rerun()

    st.markdown("---")
    st.markdown("### Scan settings")
    chain_mode = st.radio(
        "Chain coverage",
        options=["Free tier", "All chains"],
        help="Free tier: Ethereum, Arbitrum, Polygon, Gnosis, Linea, Blast, Mantle.",
    )
    chains = FREE_TIER_CHAINS if chain_mode == "Free tier" else CHAINS

    st.markdown("---")
    api_key = get_api_key()
    if api_key:
        st.markdown(
            f'<span class="status-pill">API connected · {mask_api_key(api_key)}</span>',
            unsafe_allow_html=True,
        )
    else:
        st.error(f"Missing API key. {api_key_setup_hint()}")

    render_limitations()

apply_theme(st.session_state.theme)

st.markdown(
    """
    <div class="hero">
        <h1>Wallet Balance Checker</h1>
        <p>
            Upload your wallet spreadsheet and get clean, multi-chain balance results in minutes.
            Built for large address lists with automatic rate-limit handling.
        </p>
        <div class="badge-row">
            <span class="badge">Multi-chain scan</span>
            <span class="badge">Batch processing</span>
            <span class="badge">CSV in / CSV out</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="panel">', unsafe_allow_html=True)
st.markdown('<div class="panel-title">Upload wallet sheet</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="panel-copy">CSV needs an address column: '
    '<code>public_address</code>, <code>address</code>, <code>wallet_address</code>, or <code>wallet</code>.</div>',
    unsafe_allow_html=True,
)
uploaded = st.file_uploader(
    "Drop your CSV here",
    type=["csv"],
    label_visibility="collapsed",
)
st.markdown("</div>", unsafe_allow_html=True)

if not uploaded:
    render_step_cards()

if uploaded:
    content = uploaded.read().decode("utf-8-sig")
    try:
        fieldnames, rows = read_wallet_csv_content(content)
        addresses = unique_addresses(rows)
    except ValueError as exc:
        st.error(f"Could not read CSV: {exc}")
        st.stop()

    if not rows:
        st.warning(
            "No valid wallet addresses found. Add a column named "
            "`public_address`, `address`, `wallet_address`, or `wallet`."
        )
        st.stop()

    batches = (len(addresses) + 19) // 20
    estimated_calls = len(chains) * batches
    eta_min = max(1, estimated_calls // 3 // 60)
    eta_max = max(2, eta_min + 2)

    info_col1, info_col2, info_col3 = st.columns(3)
    with info_col1:
        render_metric("Wallets loaded", len(rows))
    with info_col2:
        render_metric("Chains scanned", len(chains), accent=True)
    with info_col3:
        render_metric("Est. runtime", f"{eta_min}-{eta_max} min")

    run_left, run_center, run_right = st.columns([1, 1.2, 1])
    with run_center:
        st.markdown('<div class="scan-btn-wrap">', unsafe_allow_html=True)
        start_scan = st.button("Start balance scan", type="primary", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    if start_scan:
        if not api_key:
            st.error(f"API key not found. {api_key_setup_hint()}")
            st.stop()

        progress_bar = st.progress(0, text="Initializing scan...")
        status = st.empty()

        def on_progress(done: int, total: int, message: str) -> None:
            fraction = done / total if total else 0
            progress_bar.progress(fraction, text=f"{done}/{total} API calls completed")
            status.markdown(f"**Status:** {message}")

        client = EtherscanClient(api_key=api_key, calls_per_second=DEFAULT_CALLS_PER_SECOND)

        with st.spinner("Scanning wallet balances..."):
            balances = fetch_balances(
                client=client,
                addresses=addresses,
                chains=chains,
                on_progress=on_progress,
            )

        active_chains = get_active_chains(balances, chains, addresses)
        enriched = enrich_rows(rows, active_chains, balances)
        out_fieldnames = output_fieldnames(fieldnames, active_chains, enriched)
        csv_bytes = rows_to_csv_bytes(out_fieldnames, enriched)
        non_zero = count_wallets_with_balance(enriched)

        st.session_state.results = {
            "rows": enriched,
            "csv_bytes": csv_bytes,
            "filename": uploaded.name,
            "non_zero": non_zero,
            "fieldnames": out_fieldnames,
            "active_chains": active_chains,
        }

        progress_bar.progress(1.0, text="Scan complete")
        status.empty()

if st.session_state.results:
    results = st.session_state.results
    enriched = results["rows"]
    non_zero = count_wallets_with_balance(enriched)

    st.markdown("---")
    st.markdown("### Scan results")

    active_chains = results.get("active_chains", {})
    extra_cols = [c for c in results["fieldnames"] if c.startswith("balance_") and c != "balance_ethereum"]
    if active_chains:
        st.success(
            f"**Export columns:** `balance_ethereum`, `multichain_summary`, `balance_fetch_status`"
            + (f" + {len(extra_cols)} chain column(s) with balance" if extra_cols else "")
        )
    else:
        st.warning("No chain returned valid data. Check your API key tier or try again later.")

    r1, r2, r3 = st.columns(3)
    with r1:
        render_metric("Total wallets", len(enriched))
    with r2:
        render_metric("With balance", non_zero, accent=True)
    with r3:
        render_metric("Empty wallets", len(enriched) - non_zero)

    st.download_button(
        label="Download updated CSV",
        data=results["csv_bytes"],
        file_name=f"balances_{results['filename']}",
        mime="text/csv",
        type="primary",
        use_container_width=False,
    )

    preview_cols = results["fieldnames"]
    preview_rows = [{col: row.get(col, "") for col in preview_cols} for row in enriched[:25]]

    with st.expander("Preview first 25 rows", expanded=True):
        st.dataframe(preview_rows, use_container_width=True, hide_index=True)
