# Wallet Balance Checker

Upload a CSV of EVM wallet addresses and download balances from Etherscan (multi-chain native coins).

Built with **Streamlit** + **Etherscan API V2**.

## Run locally

```powershell
cd "wallet address automate"
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Create a `.env` file:

```
ETHERSCAN_API_KEY=your_key_here
ETHERSCAN_CALLS_PER_SECOND=3
```

## Deploy on Streamlit Cloud (free)

1. Push this repo to GitHub (see below).
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. Click **New app** → select repo `supporttechmile-dev/wallet-balance-checker`.
4. Set **Main file path** to `app.py`.
5. Open **Advanced settings** → **Secrets** and add:

```toml
ETHERSCAN_API_KEY = "your_etherscan_api_key"
```

6. Click **Deploy**.

Get a free API key: [etherscan.io/myapikey](https://etherscan.io/myapikey)

## CSV format

Your CSV needs one address column named:

- `public_address`, or
- `address`, or
- `wallet_address`, or
- `wallet`

Addresses must be EVM format (`0x` + 40 hex characters).

## Output columns

Always added:

- `balance_ethereum` — mainnet ETH (`0` if empty)
- `multichain_summary` — other chains with balance (`0` if none)
- `balance_fetch_status` — `ok` or `error`

Extra columns (e.g. `balance_arbitrum`) appear only when that wallet has a non-zero balance on that chain.

## CLI (optional)

```powershell
python fetch_wallet_balances.py --input wallets_rows.csv --output results.csv
```
