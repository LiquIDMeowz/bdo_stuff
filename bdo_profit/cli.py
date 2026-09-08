import argparse
import csv as csv_module
import time
from pathlib import Path

from bdo_profit import cache, config, price_cache
from bdo_profit.market_client import MarketClient
from bdo_profit.profitability import (
    PathResult,
    all_item_ids,
    apply_bonus_rate_overrides,
    best_path_value,
    build_edges_by_input,
    category_reachable_items,
    identify_raw_materials,
)
from bdo_profit.scraper.bdocodex import scrape_all

CATEGORY_PROCESS_TYPES = {
    "ore": {"Heating"},
    "wood": {"Chopping"},
    "cooking": {"Cooking"},
    "alchemy": {"Alchemy", "Simple Alchemy"},
}

DEFAULT_CACHE_PATH = Path("data/recipes_cache.json")
DEFAULT_BONUS_PATH = Path("config/bonus_proc_rates.yaml")
DEFAULT_NPC_PATH = Path("data/npc_prices.json")
DEFAULT_PRICE_CACHE_PATH = Path("data/price_cache.json")
DEFAULT_PRICE_CACHE_TTL = 3600.0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bdo_profit")
    parser.add_argument(
        "--category", choices=["ore", "wood", "cooking", "alchemy", "all"], default="all"
    )
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--refresh-recipes", action="store_true")
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--tax-rate", type=float, default=0.65)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--bonus-rates-path", type=Path, default=DEFAULT_BONUS_PATH)
    parser.add_argument("--npc-prices-path", type=Path, default=DEFAULT_NPC_PATH)
    parser.add_argument("--price-cache-path", type=Path, default=DEFAULT_PRICE_CACHE_PATH)
    parser.add_argument(
        "--price-cache-ttl",
        type=float,
        default=DEFAULT_PRICE_CACHE_TTL,
        help="Reuse a cached price younger than this many seconds (default 3600 = 1 hour)",
    )
    parser.add_argument(
        "--fresh-prices",
        action="store_true",
        help="Ignore the on-disk price cache and refetch every price live",
    )
    parser.add_argument(
        "--category-hops",
        type=int,
        default=2,
        help=(
            "How many hops from a matching-category recipe to scope price-fetching "
            "to under --category (default 2). Common staple ingredients (Water, "
            "Squid...) bridge huge parts of the graph together after a few hops, so "
            "a small bound keeps scoped runs fast; use a larger value or a negative "
            "number for unbounded if you suspect a relevant chain is being missed."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)

    if args.refresh_recipes or not args.cache_path.exists():
        edges = scrape_all()
        cache.save_edges(edges, args.cache_path)
    else:
        edges, _scraped_at = cache.load_edges(args.cache_path)

    npc_prices = config.load_npc_prices(args.npc_prices_path)
    bonus_rates = config.load_bonus_proc_rates(args.bonus_rates_path)
    edges = apply_bonus_rate_overrides(edges, bonus_rates)

    raw_material_ids = identify_raw_materials(edges)
    edges_by_input = build_edges_by_input(edges)

    if args.category == "all":
        wanted_ids = all_item_ids(edges)
    else:
        hop_bound = None if args.category_hops < 0 else args.category_hops
        wanted_ids = all_item_ids(edges) & category_reachable_items(
            edges, CATEGORY_PROCESS_TYPES[args.category], max_hops=hop_bound
        )

    # Always load existing cache (even with --fresh-prices) so a scoped
    # --category run never discards other categories' previously cached
    # entries when it saves back -- --fresh-prices only means "don't treat
    # anything as fresh enough to reuse this run", not "forget history".
    disk_price_cache = price_cache.load_price_cache(args.price_cache_path)

    client = MarketClient()
    prices: dict[int, float] = {}
    names: dict[int, str] = {}
    stocks: dict[int, int] = {}
    trades: dict[int, int] = {}
    item_ids = sorted(wanted_ids)
    total = len(item_ids)
    failed = 0
    fetched_live = 0
    now = time.time()
    for i, item_id in enumerate(item_ids):
        cached_snapshot = None if args.fresh_prices else disk_price_cache.get(item_id)
        if cached_snapshot and (now - cached_snapshot.fetched_at) < args.price_cache_ttl:
            snapshot = cached_snapshot
        else:
            snapshot = client.get_price(item_id)
            fetched_live += 1
            if snapshot:
                disk_price_cache[item_id] = snapshot
        if snapshot:
            prices[item_id] = snapshot.price
            names[item_id] = snapshot.name
            stocks[item_id] = snapshot.current_stock
            trades[item_id] = snapshot.total_trades
        else:
            failed += 1
        # Save incrementally, not only at the end -- a long run (thousands of
        # rate-limited requests) can take hours, and a timeout/crash midway
        # must not lose everything fetched so far.
        if (i + 1) % 50 == 0:
            price_cache.save_price_cache(disk_price_cache, args.price_cache_path)
        if (i + 1) % 100 == 0:
            print(
                f"  processed {i + 1}/{total} prices "
                f"({fetched_live} fetched live, {failed} unavailable so far)..."
            )
    if failed:
        print(
            f"WARNING: {failed}/{total} item prices unavailable "
            f"(API errors or no market data) -- affected items excluded from pricing"
        )
    price_cache.save_price_cache(disk_price_cache, args.price_cache_path)

    memo: dict[int, PathResult] = {}
    results = [
        best_path_value(item_id, edges_by_input, prices, npc_prices, args.tax_rate, memo)
        for item_id in raw_material_ids
    ]

    if args.category != "all":
        allowed = CATEGORY_PROCESS_TYPES[args.category]
        # "sell raw" results have no process types but are still valid answers
        # under a category filter -- the point of the tool is comparing them.
        results = [
            r for r in results
            if r.action == "sell_raw" or set(r.process_types) & allowed
        ]

    results.sort(key=lambda r: r.value_per_unit, reverse=True)
    results = results[: args.top]

    _print_table(results, names, stocks, trades, args.category)

    if args.csv:
        _write_csv(results, names, stocks, trades, args.csv)


def _print_table(
    results: list[PathResult],
    names: dict[int, str],
    stocks: dict[int, int],
    trades: dict[int, int],
    category: str,
) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title=f"BDO Profitability ({category})")
    table.add_column("Item")
    table.add_column("Best Action")
    table.add_column("Value/unit", justify="right")
    table.add_column("Upside if procs/unit", justify="right")
    table.add_column("Stock", justify="right")
    table.add_column("Trades", justify="right")
    for r in results:
        table.add_row(
            names.get(r.item_id, f"Item {r.item_id}"),
            r.action,
            f"{r.value_per_unit:,.0f}",
            f"+{r.bonus_upside_per_unit:,.0f}",
            f"{stocks.get(r.item_id, 0):,}",
            f"{trades.get(r.item_id, 0):,}",
        )
    Console().print(table)


def _write_csv(
    results: list[PathResult],
    names: dict[int, str],
    stocks: dict[int, int],
    trades: dict[int, int],
    path: Path,
) -> None:
    with path.open("w", newline="") as f:
        writer = csv_module.writer(f)
        writer.writerow(
            [
                "item",
                "best_action",
                "value_per_unit",
                "upside_if_procs_per_unit",
                "current_stock",
                "total_trades",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    names.get(r.item_id, f"Item {r.item_id}"),
                    r.action,
                    r.value_per_unit,
                    r.bonus_upside_per_unit,
                    stocks.get(r.item_id, 0),
                    trades.get(r.item_id, 0),
                ]
            )
