#!/usr/bin/env python3
"""
Fetch native token balances across multiple EVM chains using Etherscan API V2.

Reads wallet addresses from a CSV, queries balances in batches (up to 20 addresses
per call per chain), and writes an updated CSV with per-chain and total balance data.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

try:
    import requests
except ImportError:
    print("Missing dependency: requests. Install with: pip install -r requirements.txt")
    sys.exit(1)


API_BASE_URL = "https://api.etherscan.io/v2/api"
BATCH_SIZE = 20
DEFAULT_CALLS_PER_SECOND = 3.0
MAX_RETRIES = 5
CHECKPOINT_FILENAME = ".balance_checkpoint.json"

# Chains commonly shown in Etherscan's "Multi-Chain Info" section.
# Each entry: chainid, native symbol, decimals, free-tier availability on Etherscan API.
CHAINS: dict[str, dict[str, Any]] = {
    "ethereum": {"chainid": 1, "symbol": "ETH", "decimals": 18, "free_tier": True},
    "arbitrum": {"chainid": 42161, "symbol": "ETH", "decimals": 18, "free_tier": True},
    "optimism": {"chainid": 10, "symbol": "ETH", "decimals": 18, "free_tier": False},
    "base": {"chainid": 8453, "symbol": "ETH", "decimals": 18, "free_tier": False},
    "polygon": {"chainid": 137, "symbol": "POL", "decimals": 18, "free_tier": True},
    "bsc": {"chainid": 56, "symbol": "BNB", "decimals": 18, "free_tier": False},
    "avalanche": {"chainid": 43114, "symbol": "AVAX", "decimals": 18, "free_tier": False},
    "gnosis": {"chainid": 100, "symbol": "xDAI", "decimals": 18, "free_tier": True},
    "linea": {"chainid": 59144, "symbol": "ETH", "decimals": 18, "free_tier": True},
    "scroll": {"chainid": 534352, "symbol": "ETH", "decimals": 18, "free_tier": False},
    "zksync": {"chainid": 324, "symbol": "ETH", "decimals": 18, "free_tier": False},
    "blast": {"chainid": 81457, "symbol": "ETH", "decimals": 18, "free_tier": True},
    "mantle": {"chainid": 5000, "symbol": "MNT", "decimals": 18, "free_tier": True},
}

# Subset typically available on the Etherscan free API key.
FREE_TIER_CHAINS: dict[str, dict[str, Any]] = {
    key: CHAINS[key] for key in CHAINS if CHAINS[key].get("free_tier")
}

REQUIRED_OUTPUT_COLUMNS = ["balance_ethereum", "multichain_summary", "balance_fetch_status"]
EXCLUDED_OUTPUT_COLUMNS = {"balance"}


@dataclass
class RateLimiter:
    calls_per_second: float

    def __post_init__(self) -> None:
        self._min_interval = 1.0 / self.calls_per_second
        self._last_call = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()


class EtherscanClient:
    def __init__(self, api_key: str, calls_per_second: float = DEFAULT_CALLS_PER_SECOND) -> None:
        self.api_key = api_key
        self.session = requests.Session()
        self.rate_limiter = RateLimiter(calls_per_second)

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        payload = {**params, "apikey": self.api_key}

        for attempt in range(1, MAX_RETRIES + 1):
            self.rate_limiter.wait()
            try:
                response = self.session.get(API_BASE_URL, params=payload, timeout=30)
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"HTTP request failed after {MAX_RETRIES} attempts: {exc}") from exc
                backoff = min(2 ** attempt, 30)
                logging.warning("Request error (%s). Retrying in %ss...", exc, backoff)
                time.sleep(backoff)
                continue

            message = str(data.get("message", ""))
            result = data.get("result")

            if data.get("status") == "1":
                return data

            rate_limited = (
                "rate limit" in message.lower()
                or (isinstance(result, str) and "rate limit" in result.lower())
            )
            if rate_limited and attempt < MAX_RETRIES:
                backoff = min(2 ** attempt, 30)
                logging.warning("Rate limit hit. Retrying in %ss...", backoff)
                time.sleep(backoff)
                continue

            raise RuntimeError(
                f"Etherscan API error (chainid={params.get('chainid')}): "
                f"status={data.get('status')} message={message} result={result}"
            )

        raise RuntimeError("Unexpected request loop exit")

    def get_balances_multi(self, chainid: int, addresses: list[str]) -> dict[str, int]:
        if not addresses:
            return {}

        joined = ",".join(addresses)
        data = self._request(
            {
                "chainid": chainid,
                "module": "account",
                "action": "balancemulti",
                "address": joined,
                "tag": "latest",
            }
        )

        balances: dict[str, int] = {}
        for item in data.get("result", []):
            account = str(item.get("account", "")).lower()
            try:
                balances[account] = int(item.get("balance", "0"))
            except (TypeError, ValueError):
                balances[account] = 0
        return balances


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def wei_to_decimal(wei: int, decimals: int = 18) -> Decimal:
    scale = Decimal(10) ** decimals
    return Decimal(wei) / scale


def format_decimal(value: Decimal, max_places: int = 8) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return format(normalized.quantize(Decimal(1)), "f")
    formatted = f"{normalized:.{max_places}f}".rstrip("0").rstrip(".")
    return formatted or "0"


def read_wallet_csv(input_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with input_path.open(newline="", encoding="utf-8-sig") as handle:
        return read_wallet_csv_file(handle)


def read_wallet_csv_content(content: str) -> tuple[list[str], list[dict[str, str]]]:
    import io

    return read_wallet_csv_file(io.StringIO(content))


def resolve_address_column(fieldnames: list[str | None]) -> str:
    candidates = [
        "public_address",
        "address",
        "wallet_address",
        "wallet",
        "eth_address",
        "ethereum_address",
    ]
    lower_to_original = {name.lower().strip(): name for name in fieldnames if name}
    for candidate in candidates:
        if candidate in lower_to_original:
            return lower_to_original[candidate]
    raise ValueError(
        "No address column found. Use one of: public_address, address, wallet_address, wallet."
    )


def read_wallet_csv_file(handle) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(handle)
    if not reader.fieldnames:
        raise ValueError("No header row found in CSV")

    fieldnames = list(reader.fieldnames)
    address_column = resolve_address_column(fieldnames)
    rows: list[dict[str, str]] = []

    for row in reader:
        cleaned = {key: (value if value is not None else "") for key, value in row.items()}
        address = cleaned.get(address_column, "").strip()
        if not address:
            continue
        if not address.startswith("0x") or len(address) != 42:
            logging.warning("Skipping invalid address: %s", address)
            continue
        cleaned["public_address"] = address
        rows.append(cleaned)

    return fieldnames, rows


def unique_addresses(rows: list[dict[str, str]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        address = row["public_address"].strip()
        key = address.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(address)
    return ordered


def chunk_list(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def load_checkpoint(checkpoint_path: Path) -> dict[str, dict[str, int]]:
    if not checkpoint_path.exists():
        return {}
    try:
        raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        return {chain: {addr.lower(): int(balance) for addr, balance in balances.items()} for chain, balances in raw.items()}
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logging.warning("Could not read checkpoint (%s). Starting fresh.", exc)
        return {}


def save_checkpoint(checkpoint_path: Path, balances: dict[str, dict[str, int]]) -> None:
    serializable = {
        chain: {address: str(balance) for address, balance in chain_balances.items()}
        for chain, chain_balances in balances.items()
    }
    checkpoint_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")


def fetch_balances(
    client: EtherscanClient,
    addresses: list[str],
    chains: dict[str, dict[str, Any]],
    checkpoint_path: Path | None = None,
    resume: bool = False,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, dict[str, int]]:
    all_balances: dict[str, dict[str, int]] = defaultdict(dict)

    if resume and checkpoint_path:
        existing = load_checkpoint(checkpoint_path)
        for chain, chain_balances in existing.items():
            all_balances[chain].update(chain_balances)
        if existing:
            logging.info("Loaded checkpoint with %d chain(s) already fetched.", len(existing))

    batches = chunk_list(addresses, BATCH_SIZE)
    total_calls = len(chains) * len(batches)
    completed_calls = 0

    def report(message: str) -> None:
        if on_progress:
            on_progress(completed_calls, total_calls, message)

    for chain_key, chain_info in chains.items():
        chainid = int(chain_info["chainid"])
        logging.info("Fetching balances for %s (chainid=%s)...", chain_key, chainid)
        report(f"Fetching {chain_key}...")

        for batch_index, batch in enumerate(batches, start=1):
            batch_keys = [address.lower() for address in batch]
            if resume and checkpoint_path and all(
                address_key in all_balances.get(chain_key, {}) for address_key in batch_keys
            ):
                completed_calls += 1
                report(f"Skipped cached {chain_key} batch {batch_index}/{len(batches)}")
                continue

            try:
                batch_balances = client.get_balances_multi(chainid, batch)
            except RuntimeError as exc:
                logging.error(
                    "Failed on %s batch %d/%d: %s",
                    chain_key,
                    batch_index,
                    len(batches),
                    exc,
                )
                for address in batch:
                    all_balances[chain_key].setdefault(address.lower(), -1)
                completed_calls += 1
                if checkpoint_path:
                    save_checkpoint(checkpoint_path, all_balances)
                report(f"Error on {chain_key}: {exc}")
                continue

            for address in batch:
                key = address.lower()
                all_balances[chain_key][key] = batch_balances.get(key, 0)

            completed_calls += 1
            if checkpoint_path:
                save_checkpoint(checkpoint_path, all_balances)
            report(f"Completed {chain_key} batch {batch_index}/{len(batches)}")

            if completed_calls % 10 == 0 or completed_calls == total_calls:
                logging.info("Progress: %d/%d API calls completed", completed_calls, total_calls)

    return all_balances


def get_active_chains(
    balances: dict[str, dict[str, int]],
    chains: dict[str, dict[str, Any]],
    addresses: list[str],
) -> dict[str, dict[str, Any]]:
    """Return chains that returned valid balance data for every address."""
    address_keys = [address.lower() for address in addresses]
    active: dict[str, dict[str, Any]] = {}

    for chain_key, chain_info in chains.items():
        chain_data = balances.get(chain_key)
        if not chain_data:
            continue
        if all(chain_data.get(key, -1) >= 0 for key in address_keys):
            active[chain_key] = chain_info

    return active


def chain_balance_column(chain_key: str) -> str:
    return f"balance_{chain_key}"


def build_multichain_summary(
    address_key: str,
    chains: dict[str, dict[str, Any]],
    balances: dict[str, dict[str, int]],
    *,
    include_ethereum: bool = False,
) -> str:
    parts: list[str] = []
    for chain_key, chain_info in chains.items():
        if not include_ethereum and chain_key == "ethereum":
            continue
        wei = balances.get(chain_key, {}).get(address_key, 0)
        if wei <= 0:
            continue
        amount = format_decimal(wei_to_decimal(wei, int(chain_info["decimals"])))
        parts.append(f"{chain_info['symbol']}@{chain_key}: {amount}")
    return " | ".join(parts) if parts else "0"


def enrich_rows(
    rows: list[dict[str, str]],
    active_chains: dict[str, dict[str, Any]],
    balances: dict[str, dict[str, int]],
) -> list[dict[str, str]]:
    enriched: list[dict[str, str]] = []

    for row in rows:
        updated = dict(row)
        address_key = row["public_address"].strip().lower()

        for chain_key, chain_info in active_chains.items():
            if chain_key == "ethereum":
                continue
            wei = balances[chain_key].get(address_key, 0)
            if wei <= 0:
                continue
            amount = wei_to_decimal(wei, int(chain_info["decimals"]))
            updated[chain_balance_column(chain_key)] = format_decimal(amount)

        if "ethereum" in active_chains:
            eth_wei = balances["ethereum"].get(address_key, 0)
            eth_amount = wei_to_decimal(eth_wei, int(CHAINS["ethereum"]["decimals"]))
            updated["balance_ethereum"] = format_decimal(eth_amount)
        else:
            updated["balance_ethereum"] = "0"

        updated["multichain_summary"] = build_multichain_summary(
            address_key, active_chains, balances, include_ethereum=False
        )
        updated["balance_fetch_status"] = "ok" if active_chains else "error"
        enriched.append(updated)

    return enriched


def optional_chain_columns(
    enriched_rows: list[dict[str, str]],
    active_chains: dict[str, dict[str, Any]],
) -> list[str]:
    columns: list[str] = []
    for chain_key in active_chains:
        if chain_key == "ethereum":
            continue
        column = chain_balance_column(chain_key)
        if any(column in row for row in enriched_rows):
            columns.append(column)
    return columns


def output_fieldnames(
    original_fieldnames: list[str],
    active_chains: dict[str, dict[str, Any]] | None = None,
    enriched_rows: list[dict[str, str]] | None = None,
) -> list[str]:
    preserved = [
        name
        for name in original_fieldnames
        if name is not None and name not in EXCLUDED_OUTPUT_COLUMNS
    ]
    extra_chain_columns = optional_chain_columns(enriched_rows or [], active_chains or {})

    for column in ["balance_ethereum"] + extra_chain_columns + ["multichain_summary", "balance_fetch_status"]:
        if column not in preserved:
            preserved.append(column)

    return preserved


def wallet_has_balance(row: dict[str, str]) -> bool:
    if Decimal(row.get("balance_ethereum", "0") or "0") > 0:
        return True
    if row.get("multichain_summary", "0") not in ("", "0"):
        return True
    for key, value in row.items():
        if not key.startswith("balance_") or key in {"balance_ethereum", "balance_fetch_status"}:
            continue
        if Decimal(value or "0") > 0:
            return True
    return False


def count_wallets_with_balance(rows: list[dict[str, str]]) -> int:
    return sum(1 for row in rows if wallet_has_balance(row))


def write_wallet_csv(output_path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        write_wallet_csv_file(handle, fieldnames, rows)


def write_wallet_csv_file(handle, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)


def rows_to_csv_bytes(fieldnames: list[str], rows: list[dict[str, str]]) -> bytes:
    import io

    buffer = io.StringIO()
    write_wallet_csv_file(buffer, fieldnames, rows)
    return buffer.getvalue().encode("utf-8-sig")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch multi-chain wallet balances from Etherscan API V2 and update a CSV."
    )
    parser.add_argument(
        "--input",
        default="wallets_rows.csv",
        help="Input CSV file (default: wallets_rows.csv)",
    )
    parser.add_argument(
        "--output",
        default="wallets_rows_with_balances.csv",
        help="Output CSV file (default: wallets_rows_with_balances.csv)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ETHERSCAN_API_KEY", ""),
        help="Etherscan API key (or set ETHERSCAN_API_KEY env var / .env file)",
    )
    parser.add_argument(
        "--calls-per-second",
        type=float,
        default=float(os.environ.get("ETHERSCAN_CALLS_PER_SECOND", DEFAULT_CALLS_PER_SECOND)),
        help=f"API rate limit throttle (default: {DEFAULT_CALLS_PER_SECOND})",
    )
    parser.add_argument(
        "--chains",
        default="all",
        help="Comma-separated chain keys to query, or 'all' (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only the first N addresses (0 = all)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore checkpoint file and refetch all balances",
    )
    parser.add_argument(
        "--keep-checkpoint",
        action="store_true",
        help="Keep checkpoint file after successful completion",
    )
    return parser.parse_args()


def resolve_chains(selection: str) -> dict[str, dict[str, Any]]:
    if selection.strip().lower() == "all":
        return CHAINS

    selected: dict[str, dict[str, Any]] = {}
    for key in selection.split(","):
        chain_key = key.strip().lower()
        if not chain_key:
            continue
        if chain_key not in CHAINS:
            valid = ", ".join(sorted(CHAINS))
            raise ValueError(f"Unknown chain '{chain_key}'. Valid options: {valid}")
        selected[chain_key] = CHAINS[chain_key]
    if not selected:
        raise ValueError("No valid chains selected.")
    return selected


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    script_dir = Path(__file__).resolve().parent
    load_env_file(script_dir / ".env")

    api_key = args.api_key or os.environ.get("ETHERSCAN_API_KEY", "")
    if not api_key:
        logging.error(
            "No API key provided. Use --api-key, set ETHERSCAN_API_KEY, or create a .env file."
        )
        return 1

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = script_dir / input_path

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = script_dir / output_path

    if not input_path.exists():
        logging.error("Input file not found: %s", input_path)
        return 1

    try:
        chains = resolve_chains(args.chains)
    except ValueError as exc:
        logging.error("%s", exc)
        return 1

    original_fieldnames, rows = read_wallet_csv(input_path)
    addresses = unique_addresses(rows)
    if args.limit > 0:
        allowed = {address.lower() for address in addresses[: args.limit]}
        rows = [row for row in rows if row["public_address"].strip().lower() in allowed]
        addresses = addresses[: args.limit]

    if not addresses:
        logging.error("No valid wallet addresses found in %s", input_path)
        return 1

    logging.info("Loaded %d wallet address(es) from %s", len(addresses), input_path.name)
    logging.info(
        "Estimated API calls: %d (%d chains x %d batches of up to %d addresses)",
        len(chains) * ((len(addresses) + BATCH_SIZE - 1) // BATCH_SIZE),
        len(chains),
        (len(addresses) + BATCH_SIZE - 1) // BATCH_SIZE,
        BATCH_SIZE,
    )

    client = EtherscanClient(api_key=api_key, calls_per_second=args.calls_per_second)
    checkpoint_path = script_dir / CHECKPOINT_FILENAME

    if args.no_resume and checkpoint_path.exists():
        checkpoint_path.unlink()

    try:
        balances = fetch_balances(
            client=client,
            addresses=addresses,
            chains=chains,
            checkpoint_path=checkpoint_path,
            resume=not args.no_resume,
        )
    except KeyboardInterrupt:
        logging.warning("Interrupted. Progress saved to %s", checkpoint_path.name)
        return 130

    active_chains = get_active_chains(balances, chains, addresses)
    enriched_rows = enrich_rows(rows, active_chains, balances)
    fieldnames = output_fieldnames(original_fieldnames, active_chains, enriched_rows)
    write_wallet_csv(output_path, fieldnames, enriched_rows)

    if checkpoint_path.exists() and not args.keep_checkpoint:
        checkpoint_path.unlink()

    non_zero = count_wallets_with_balance(enriched_rows)
    logging.info("Active chains in export: %s", ", ".join(active_chains) or "none")
    logging.info("Done. Wrote %d row(s) to %s", len(enriched_rows), output_path.name)
    logging.info("Addresses with non-zero balance on at least one chain: %d", non_zero)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
