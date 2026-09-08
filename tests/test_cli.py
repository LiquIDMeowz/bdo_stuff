import time
from pathlib import Path
from unittest.mock import patch

from bdo_profit.cli import main
from bdo_profit.market_client import MarketSnapshot
from bdo_profit.models import ConversionEdge, YieldRange
from bdo_profit.price_cache import save_price_cache


FAKE_EDGES = [
    ConversionEdge(
        recipe_id=1,
        name="Melted Iron Shard",
        process_type="Heating",
        mastery_required=0,
        inputs=((4001, 5.0),),
        base_outputs=(YieldRange(4051, 1.0, 4.0),),
    )
]


def _snapshot(item_id, name, price, current_stock=0, total_trades=0):
    return MarketSnapshot(
        item_id=item_id,
        sub_id=0,
        name=name,
        price=price,
        current_stock=current_stock,
        total_trades=total_trades,
        last_sold_price=price,
        fetched_at=time.time(),
    )


def _fake_client(snapshots: dict, calls: list[int] | None = None):
    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def get_price(self, item_id, sub_id=0):
            if calls is not None:
                calls.append(item_id)
            return snapshots.get(item_id)

    return FakeClient


def _common_paths(tmp_path: Path) -> dict:
    npc_path = tmp_path / "npc.json"
    npc_path.write_text("{}")
    bonus_path = tmp_path / "bonus.yaml"
    bonus_path.write_text("")
    return {
        "cache_path": tmp_path / "cache.json",
        "npc_path": npc_path,
        "bonus_path": bonus_path,
        "csv_path": tmp_path / "out.csv",
        "price_cache_path": tmp_path / "prices.json",
    }


def _run(paths: dict, extra_args: list[str] | None = None) -> None:
    main(
        [
            "--refresh-recipes",
            "--cache-path", str(paths["cache_path"]),
            "--npc-prices-path", str(paths["npc_path"]),
            "--bonus-rates-path", str(paths["bonus_path"]),
            "--csv", str(paths["csv_path"]),
            "--top", "5",
            "--price-cache-path", str(paths["price_cache_path"]),
        ]
        + (extra_args or [])
    )


def test_main_prints_ranked_table_and_writes_csv(tmp_path: Path, capsys):
    paths = _common_paths(tmp_path)
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 100.0),
        4051: _snapshot(4051, "Melted Iron Shard", 2000.0),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=FAKE_EDGES):
        _run(paths)

    captured = capsys.readouterr()
    assert "Iron Ore" in captured.out
    assert paths["csv_path"].exists()
    assert "Iron Ore" in paths["csv_path"].read_text()


def test_sell_raw_result_survives_category_filter(tmp_path: Path, capsys):
    """A raw material whose best action is 'sell raw' must not be filtered out.

    The edge here is process_type "Heating", i.e. in-category for --category ore,
    so the item would qualify if processing won. Pricing makes the raw sale the
    better play instead -- and that answer still has to reach the output.
    """
    paths = _common_paths(tmp_path)
    # Ore is valuable, the smelted product is near-worthless -> sell raw wins.
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 1_000_000.0),
        4051: _snapshot(4051, "Melted Iron Shard", 1.0),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=FAKE_EDGES):
        _run(paths, ["--category", "ore"])

    captured = capsys.readouterr()
    assert "Iron Ore" in captured.out
    assert "sell_raw" in captured.out
    csv_text = paths["csv_path"].read_text()
    assert "Iron Ore" in csv_text
    assert "sell_raw" in csv_text


def test_fresh_price_cache_entry_is_reused_without_refetching(tmp_path: Path, capsys):
    paths = _common_paths(tmp_path)
    save_price_cache(
        {
            4001: _snapshot(4001, "Iron Ore", 100.0, current_stock=50, total_trades=999),
            4051: _snapshot(4051, "Melted Iron Shard", 2000.0, current_stock=10, total_trades=5),
        },
        paths["price_cache_path"],
    )

    calls: list[int] = []

    class RaisingClient:
        def __init__(self, *a, **kw):
            pass

        def get_price(self, item_id, sub_id=0):
            calls.append(item_id)
            raise AssertionError("should not fetch live -- cache is fresh")

    with patch("bdo_profit.cli.MarketClient", RaisingClient), \
         patch("bdo_profit.cli.scrape_all", return_value=FAKE_EDGES):
        _run(paths, ["--price-cache-ttl", "3600"])

    assert calls == []
    assert "Iron Ore" in capsys.readouterr().out


def test_stale_price_cache_entry_is_refetched(tmp_path: Path):
    paths = _common_paths(tmp_path)
    stale_time = time.time() - 10_000  # older than the TTL below
    save_price_cache(
        {
            4001: MarketSnapshot(4001, 0, "Iron Ore", 100.0, 50, 999, 95.0, stale_time),
            4051: MarketSnapshot(4051, 0, "Melted Iron Shard", 2000.0, 10, 5, 1900.0, stale_time),
        },
        paths["price_cache_path"],
    )

    calls: list[int] = []
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 111.0),
        4051: _snapshot(4051, "Melted Iron Shard", 2222.0),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices, calls)), \
         patch("bdo_profit.cli.scrape_all", return_value=FAKE_EDGES):
        _run(paths, ["--price-cache-ttl", "3600"])

    assert set(calls) == {4001, 4051}


def test_fresh_prices_flag_forces_refetch_even_within_ttl(tmp_path: Path):
    paths = _common_paths(tmp_path)
    save_price_cache(
        {
            4001: _snapshot(4001, "Iron Ore", 100.0),
            4051: _snapshot(4051, "Melted Iron Shard", 2000.0),
        },
        paths["price_cache_path"],
    )
    calls: list[int] = []
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 111.0),
        4051: _snapshot(4051, "Melted Iron Shard", 2222.0),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices, calls)), \
         patch("bdo_profit.cli.scrape_all", return_value=FAKE_EDGES):
        _run(paths, ["--fresh-prices"])

    assert set(calls) == {4001, 4051}


def test_fresh_prices_does_not_discard_other_categories_cached_entries(tmp_path: Path):
    """A scoped --category run with --fresh-prices must not wipe out cached
    prices for items outside this run's scope when it saves the cache back."""
    paths = _common_paths(tmp_path)
    ore_edge = ConversionEdge(
        recipe_id=1,
        name="Melted Iron Shard",
        process_type="Heating",
        mastery_required=0,
        inputs=((4001, 5.0),),
        base_outputs=(YieldRange(4051, 1.0, 4.0),),
    )
    # A previously cached entry for an item entirely outside this ore-only run.
    save_price_cache(
        {9999: _snapshot(9999, "Unrelated Cooking Item", 500.0, current_stock=3, total_trades=7)},
        paths["price_cache_path"],
    )
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 100.0),
        4051: _snapshot(4051, "Melted Iron Shard", 2000.0),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=[ore_edge]):
        _run(paths, ["--category", "ore", "--fresh-prices"])

    from bdo_profit.price_cache import load_price_cache

    saved = load_price_cache(paths["price_cache_path"])
    assert 9999 in saved
    assert saved[9999].name == "Unrelated Cooking Item"


def test_category_filter_only_fetches_prices_for_reachable_items(tmp_path: Path):
    paths = _common_paths(tmp_path)
    ore_edge = ConversionEdge(
        recipe_id=1,
        name="Melted Iron Shard",
        process_type="Heating",
        mastery_required=0,
        inputs=((4001, 5.0),),
        base_outputs=(YieldRange(4051, 1.0, 4.0),),
    )
    wood_edge = ConversionEdge(
        recipe_id=2,
        name="Plank",
        process_type="Chopping",
        mastery_required=0,
        inputs=((5001, 5.0),),
        base_outputs=(YieldRange(5051, 1.0, 4.0),),
    )
    calls: list[int] = []
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 100.0),
        4051: _snapshot(4051, "Melted Iron Shard", 100.0),
        5001: _snapshot(5001, "Log", 100.0),
        5051: _snapshot(5051, "Plank", 100.0),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices, calls)), \
         patch("bdo_profit.cli.scrape_all", return_value=[ore_edge, wood_edge]):
        _run(paths, ["--category", "ore"])

    assert set(calls) == {4001, 4051}
    assert 5001 not in calls
    assert 5051 not in calls


def test_category_hops_flag_narrows_the_reachable_set(tmp_path: Path):
    paths = _common_paths(tmp_path)
    # A 3-edge chain: only the last is Heating. With --category-hops 1, the
    # far end of the chain should be out of reach and never priced.
    edges = [
        ConversionEdge(
            recipe_id=1,
            name="Step 1",
            process_type="Grinding",
            mastery_required=0,
            inputs=((6001, 1.0),),
            base_outputs=(YieldRange(6002, 1.0, 1.0),),
        ),
        ConversionEdge(
            recipe_id=2,
            name="Step 2",
            process_type="Grinding",
            mastery_required=0,
            inputs=((6002, 1.0),),
            base_outputs=(YieldRange(6003, 1.0, 1.0),),
        ),
        ConversionEdge(
            recipe_id=3,
            name="Step 3",
            process_type="Heating",
            mastery_required=0,
            inputs=((6003, 1.0),),
            base_outputs=(YieldRange(6004, 1.0, 1.0),),
        ),
    ]
    calls: list[int] = []
    fake_prices = {
        iid: _snapshot(iid, f"Item {iid}", 100.0) for iid in (6001, 6002, 6003, 6004)
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices, calls)), \
         patch("bdo_profit.cli.scrape_all", return_value=edges):
        _run(paths, ["--category", "ore", "--category-hops", "1"])

    # Seed is {6003, 6004}; 1 hop reaches 6002 but not 6001.
    assert 6001 not in calls
    assert set(calls) == {6002, 6003, 6004}


def test_negative_category_hops_means_unbounded(tmp_path: Path):
    paths = _common_paths(tmp_path)
    edges = [
        ConversionEdge(
            recipe_id=1,
            name="Step 1",
            process_type="Grinding",
            mastery_required=0,
            inputs=((6001, 1.0),),
            base_outputs=(YieldRange(6002, 1.0, 1.0),),
        ),
        ConversionEdge(
            recipe_id=2,
            name="Step 2",
            process_type="Heating",
            mastery_required=0,
            inputs=((6002, 1.0),),
            base_outputs=(YieldRange(6003, 1.0, 1.0),),
        ),
    ]
    calls: list[int] = []
    fake_prices = {iid: _snapshot(iid, f"Item {iid}", 100.0) for iid in (6001, 6002, 6003)}

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices, calls)), \
         patch("bdo_profit.cli.scrape_all", return_value=edges):
        _run(paths, ["--category", "ore", "--category-hops", "-1"])

    assert set(calls) == {6001, 6002, 6003}


def test_alchemy_category_only_prices_alchemy_reachable_items(tmp_path: Path):
    paths = _common_paths(tmp_path)
    alchemy_edge = ConversionEdge(
        recipe_id=1,
        name="Elixir of Amity",
        process_type="Alchemy",
        mastery_required=11,
        inputs=((6351, 1.0),),
        base_outputs=(YieldRange(664, 1.0, 4.0),),
    )
    wood_edge = ConversionEdge(
        recipe_id=2,
        name="Plank",
        process_type="Chopping",
        mastery_required=0,
        inputs=((5001, 5.0),),
        base_outputs=(YieldRange(5051, 1.0, 4.0),),
    )
    calls: list[int] = []
    fake_prices = {
        6351: _snapshot(6351, "Legendary Beast's Blood", 1000.0),
        664: _snapshot(664, "Elixir of Amity", 5000.0),
        5001: _snapshot(5001, "Log", 100.0),
        5051: _snapshot(5051, "Plank", 200.0),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices, calls)), \
         patch("bdo_profit.cli.scrape_all", return_value=[alchemy_edge, wood_edge]):
        _run(paths, ["--category", "alchemy"])

    assert set(calls) == {6351, 664}
    assert 5001 not in calls
    assert 5051 not in calls


def test_output_includes_stock_and_trades_columns(tmp_path: Path, capsys):
    paths = _common_paths(tmp_path)
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 100.0, current_stock=42, total_trades=13579),
        4051: _snapshot(4051, "Melted Iron Shard", 2000.0, current_stock=7, total_trades=24680),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=FAKE_EDGES):
        _run(paths)

    out = capsys.readouterr().out
    assert "Stock" in out
    assert "Trades" in out
    assert "42" in out
    assert "13,579" in out or "13579" in out
    csv_text = paths["csv_path"].read_text()
    assert "stock" in csv_text.lower()
    assert "trades" in csv_text.lower()
    assert "42" in csv_text
    assert "13579" in csv_text
