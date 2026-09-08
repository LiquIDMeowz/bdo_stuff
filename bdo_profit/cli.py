import argparse
import csv as csv_module
from pathlib import Path

from bdo_profit import cache, config
from bdo_profit.market_client import MarketClient
from bdo_profit.profitability import (
    PathResult,
    all_item_ids,
    apply_bonus_rate_overrides,
    best_path_value,
    build_edges_by_input,
    identify_raw_materials,
)
from bdo_profit.scraper.bdocodex import scrape_all

CATEGORY_PROCESS_TYPES = {
    "ore": {"Heating"},
    "wood": {"Chopping"},
    "cooking": {"Cooking"},
}

DEFAULT_CACHE_PATH = Path("data/recipes_cache.json")
DEFAULT_BONUS_PATH = Path("config/bonus_proc_rates.yaml")
DEFAULT_NPC_PATH = Path("data/npc_prices.json")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bdo_profit")
    parser.add_argument("--category", choices=["ore", "wood", "cooking", "all"], default="all")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--refresh-recipes", action="store_true")
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--tax-rate", type=float, default=0.65)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--bonus-rates-path", type=Path, default=DEFAULT_BONUS_PATH)
    parser.add_argument("--npc-prices-path", type=Path, default=DEFAULT_NPC_PATH)
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

    client = MarketClient()
    prices: dict[int, float] = {}
    names: dict[int, str] = {}
    item_ids = sorted(all_item_ids(edges))
    total = len(item_ids)
    failed = 0
    for i, item_id in enumerate(item_ids):
        snapshot = client.get_price(item_id)
        if snapshot:
            prices[item_id] = snapshot.price
            names[item_id] = snapshot.name
        else:
            failed += 1
        if (i + 1) % 100 == 0:
            print(f"  fetched {i + 1}/{total} prices ({failed} unavailable so far)...")
    if failed:
        print(
            f"WARNING: {failed}/{total} item prices unavailable "
            f"(API errors or no market data) -- affected items excluded from pricing"
        )

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

    _print_table(results, names, args.category)

    if args.csv:
        _write_csv(results, names, args.csv)


def _print_table(results: list[PathResult], names: dict[int, str], category: str) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title=f"BDO Profitability ({category})")
    table.add_column("Item")
    table.add_column("Best Action")
    table.add_column("Value/unit", justify="right")
    table.add_column("Upside if procs/unit", justify="right")
    for r in results:
        table.add_row(
            names.get(r.item_id, f"Item {r.item_id}"),
            r.action,
            f"{r.value_per_unit:,.0f}",
            f"+{r.bonus_upside_per_unit:,.0f}",
        )
    Console().print(table)


def _write_csv(results: list[PathResult], names: dict[int, str], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv_module.writer(f)
        writer.writerow(["item", "best_action", "value_per_unit", "upside_if_procs_per_unit"])
        for r in results:
            writer.writerow(
                [
                    names.get(r.item_id, f"Item {r.item_id}"),
                    r.action,
                    r.value_per_unit,
                    r.bonus_upside_per_unit,
                ]
            )
