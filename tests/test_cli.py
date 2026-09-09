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
    exchanges_path = tmp_path / "exchanges.json"
    exchanges_path.write_text("[]")
    universal_procs_path = tmp_path / "universal_procs.json"
    universal_procs_path.write_text("{}")
    return {
        "cache_path": tmp_path / "cache.json",
        "npc_path": npc_path,
        "bonus_path": bonus_path,
        "exchanges_path": exchanges_path,
        "universal_procs_path": universal_procs_path,
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
            "--exchanges-path", str(paths["exchanges_path"]),
            "--universal-procs-path", str(paths["universal_procs_path"]),
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


def test_sources_lists_every_acquisition_option_sorted_by_cost(tmp_path: Path, capsys):
    # Confirmed valuable by the user: an item with several alternative
    # recipes should show the whole landscape, not just one silently
    # chosen winner.
    paths = _common_paths(tmp_path)
    edges = [
        ConversionEdge(
            recipe_id=1,
            name="CraftTraceA",
            process_type="Heating",
            mastery_required=0,
            inputs=((9001, 1.0),),
            base_outputs=(YieldRange(5960, 1.0, 4.0),),
        ),
        ConversionEdge(
            recipe_id=2,
            name="CraftTraceB",
            process_type="Heating",
            mastery_required=0,
            inputs=((9002, 1.0),),
            base_outputs=(YieldRange(5960, 1.0, 1.0),),
        ),
    ]
    fake_prices = {
        5960: _snapshot(5960, "Trace of Nature", 247000.0, current_stock=47060),
        9001: _snapshot(9001, "Cheap Catalyst", 186000.0, current_stock=18),
        9002: _snapshot(9002, "Pricier Catalyst", 900000.0, current_stock=100),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=edges):
        _run(paths, ["--sources", "5960"])

    out = capsys.readouterr().out
    assert "Sources for Trace of Nature" in out
    assert "CraftTraceA" in out
    assert "CraftTraceB" in out
    assert "BUY (market)" in out
    # Cheapest (CraftTraceA, avg 2.5 yield from a 186k catalyst = ~74.4k/unit)
    # must be listed before the more expensive options.
    assert out.index("CraftTraceA") < out.index("CraftTraceB")
    assert out.index("CraftTraceA") < out.index("BUY (market)")


def test_sources_includes_universal_proc_farming_via_exchange(tmp_path: Path, capsys):
    # Confirmed by the user: any Cooking action has a ~2% chance at Witch's
    # Delicacy (item 9780), tradeable at NPC Liana for Milk (item 9065) at
    # 10:120. Farming the cheapest Cooking recipe for this should show up
    # as one of Milk's acquisition sources, priced by expected attempts.
    paths = _common_paths(tmp_path)
    paths["exchanges_path"].write_text(
        '[{"recipe_id": -1001, "name": "Liana Exchange", '
        '"inputs": [[9780, 10]], "outputs": [[9065, 120]]}]'
    )
    paths["universal_procs_path"].write_text(
        '{"Cooking": {"item_id": 9780, "item_name": "Witch\'s Delicacy", '
        '"chance": 0.02, "qty": 1}}'
    )
    edges = [
        ConversionEdge(
            recipe_id=1,
            name="Cheap Stew",
            process_type="Cooking",
            mastery_required=0,
            inputs=((7001, 1.0),),
            base_outputs=(YieldRange(7002, 1.0, 1.0),),
        ),
    ]
    fake_prices = {
        7001: _snapshot(7001, "Filler Ingredient", 10.0, current_stock=1000),
        7002: _snapshot(7002, "Worthless Dish", 0.0),
        9065: _snapshot(9065, "Milk", 23300.0, current_stock=10000),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=edges):
        _run(paths, ["--sources", "9065", "--tax-rate", "1.0"])

    out = capsys.readouterr().out
    assert "Sources for Milk" in out
    assert "Liana Exchange" in out
    assert "BUY (market)" in out
    # Farming: net cost/attempt = 10 (ingredient) - 0 (worthless dish) = 10,
    # / 0.02 chance = 500/proc, * 10 procs needed / 120 milk = ~41.7/unit --
    # far cheaper than Milk's real 23,300 market price, so it must rank first.
    assert out.index("Liana Exchange") < out.index("BUY (market)")


def test_explain_prints_full_step_by_step_tree_with_side_ingredients(tmp_path: Path, capsys):
    paths = _common_paths(tmp_path)
    edges = [
        ConversionEdge(
            recipe_id=1,
            name="Melted Iron Shard",
            process_type="Heating",
            mastery_required=0,
            inputs=((4001, 5.0),),
            base_outputs=(YieldRange(4051, 2.0, 2.0),),
        ),
        ConversionEdge(
            recipe_id=2,
            name="Iron Ingot",
            process_type="Heating",
            mastery_required=0,
            inputs=((4051, 2.0), (9999, 1.0)),
            base_outputs=(YieldRange(4052, 1.0, 1.0),),
        ),
    ]
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 100.0),
        4051: _snapshot(4051, "Melted Iron Shard", 1.0),  # near-worthless raw -> processing wins
        4052: _snapshot(4052, "Iron Ingot", 10_000.0),
        9999: _snapshot(9999, "Flux", 50.0, current_stock=500),  # real stock -> actually buyable
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=edges):
        _run(paths, ["--explain", "4001", "--qty", "20"])

    out = capsys.readouterr().out
    assert "Iron Ore" in out
    assert "Melted Iron Shard" in out
    assert "Iron Ingot" in out
    assert "Flux" in out  # side ingredient shown, not just the main chain


def test_explain_recommends_stopping_before_a_risky_step(tmp_path: Path, capsys):
    # Confirmed valuable by the user with a real example (Copper Ore): the
    # first two steps are safe ore smelting with no side ingredients, the
    # third pulls in an expensive, thin-margin ingredient. The tool should
    # recommend stopping before the risky step, not just warn about it.
    paths = _common_paths(tmp_path)
    edges = [
        ConversionEdge(
            recipe_id=70, name="MakeShard", process_type="Heating", mastery_required=0,
            inputs=((1, 5.0),), base_outputs=(YieldRange(2, 2.0, 2.0),),
        ),
        ConversionEdge(
            recipe_id=71, name="MakeIngot", process_type="Heating", mastery_required=0,
            inputs=((2, 3.0),), base_outputs=(YieldRange(3, 1.0, 1.0),),
        ),
        ConversionEdge(
            recipe_id=72, name="MakeCrystal", process_type="Alchemy", mastery_required=0,
            inputs=((3, 1.0), (10, 2.0)), base_outputs=(YieldRange(4, 1.0, 2.0),),
        ),
    ]
    fake_prices = {
        1: _snapshot(1, "Copper Ore", 10.0),
        2: _snapshot(2, "Melted Copper Shard", 100.0),
        3: _snapshot(3, "Copper Ingot", 1000.0),
        4: _snapshot(4, "Pure Copper Crystal", 1600.0),
        10: _snapshot(10, "Metal Solvent", 500.0, current_stock=50000),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=edges):
        _run(paths, ["--explain", "1", "--qty", "15"])

    out = capsys.readouterr().out
    assert "RECOMMENDED: stop after step 2" in out
    assert "Copper Ingot" in out
    assert "BEYOND RECOMMENDED STOP" in out


def test_explain_excludes_a_chain_that_needs_a_zero_stock_ingredient(tmp_path: Path, capsys):
    # Confirmed by the user: an ingredient with zero current sell listings
    # (e.g. Bottle of Sea Water -- trivially self-gatherable, nobody bothers
    # listing it) shouldn't just be warned about -- a chain that only wins
    # through it must lose to a shorter, actually-achievable one.
    paths = _common_paths(tmp_path)
    edges = [
        ConversionEdge(
            recipe_id=1,
            name="Melted Iron Shard",
            process_type="Heating",
            mastery_required=0,
            inputs=((4001, 5.0),),
            base_outputs=(YieldRange(4051, 2.0, 2.0),),
        ),
        ConversionEdge(
            recipe_id=2,
            name="Iron Ingot",
            process_type="Heating",
            mastery_required=0,
            inputs=((4051, 2.0), (9999, 1.0)),
            base_outputs=(YieldRange(4052, 1.0, 1.0),),
        ),
    ]
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 100.0),
        4051: _snapshot(4051, "Melted Iron Shard", 1.0),
        4052: _snapshot(4052, "Iron Ingot", 10_000.0),
        9999: _snapshot(9999, "Flux", 50.0, current_stock=0),  # zero stock -> unavailable
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=edges):
        _run(paths, ["--explain", "4001", "--qty", "20"])

    out = capsys.readouterr().out
    assert "Iron Ore" in out
    assert "sell raw" in out
    assert "Iron Ingot" not in out


def test_explain_warns_when_worst_case_yield_is_a_loss(tmp_path: Path, capsys):
    # Confirmed by the user with a real example (Metal Solvent): a recipe
    # can be profitable on bdocodex's average yield while being a real loss
    # at the minimum end of the range -- must be flagged, not just averaged.
    paths = _common_paths(tmp_path)
    edges = [
        ConversionEdge(
            recipe_id=1,
            name="Melted Iron Shard",
            process_type="Heating",
            mastery_required=0,
            inputs=((4001, 5.0),),
            base_outputs=(YieldRange(4051, 2.0, 2.0),),
        ),
        ConversionEdge(
            recipe_id=2,
            name="Metal Solvent",
            process_type="Alchemy",
            mastery_required=0,
            inputs=((4051, 3.0), (9999, 2.0)),
            base_outputs=(YieldRange(4076, 1.0, 2.0),),  # avg 1.5, worst case 1
        ),
    ]
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 2710.0),
        4051: _snapshot(4051, "Melted Iron Shard", 11200.0),
        4076: _snapshot(4076, "Metal Solvent", 432000.0),
        9999: _snapshot(9999, "Trace of Nature", 247000.0, current_stock=47060),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=edges):
        _run(paths, ["--explain", "4001", "--qty", "5"])

    out = capsys.readouterr().out
    assert "Worst case" in out
    assert "WARNING" in out
    assert "worse than just selling raw" in out


def test_explain_warns_when_needed_quantity_exceeds_current_sell_listings(tmp_path: Path, capsys):
    # Confirmed by the user: current_stock is sell-side listings, not buy
    # orders -- needing far more than what's currently listed means the
    # purchase may not be fillable soon even at the shown price.
    paths = _common_paths(tmp_path)
    edges = [
        ConversionEdge(
            recipe_id=1,
            name="Melted Iron Shard",
            process_type="Heating",
            mastery_required=0,
            inputs=((4001, 5.0), (9999, 1.0)),
            base_outputs=(YieldRange(4051, 2.0, 2.0),),
        ),
    ]
    fake_prices = {
        4001: _snapshot(4001, "Iron Ore", 100.0),
        4051: _snapshot(4051, "Melted Iron Shard", 10_000.0),
        9999: _snapshot(9999, "Scarce Flux", 500.0, current_stock=3),
    }

    with patch("bdo_profit.cli.MarketClient", _fake_client(fake_prices)), \
         patch("bdo_profit.cli.scrape_all", return_value=edges):
        _run(paths, ["--explain", "4001", "--qty", "50"])

    out = capsys.readouterr().out
    assert "Scarce Flux" in out
    assert "WARNING" in out
    assert "current sell listings" in out
    assert "BUY" in out  # Flux has no recipe -> must be bought


def test_price_cache_is_saved_incrementally_not_only_at_the_end(tmp_path: Path):
    """If the process is killed mid-run (timeout, crash), prices fetched so
    far must already be on disk -- not lost by only saving once at the end."""
    paths = _common_paths(tmp_path)
    edges = [
        ConversionEdge(
            recipe_id=i,
            name=f"Step {i}",
            process_type="Heating",
            mastery_required=0,
            inputs=((7000 + i, 1.0),),
            base_outputs=(YieldRange(7100 + i, 1.0, 1.0),),
        )
        for i in range(60)
    ]
    fake_prices = {}
    for i in range(60):
        fake_prices[7000 + i] = _snapshot(7000 + i, f"Raw {i}", 100.0)
        fake_prices[7100 + i] = _snapshot(7100 + i, f"Product {i}", 200.0)

    class CrashingClient:
        def __init__(self, *a, **kw):
            self._calls = 0

        def get_price(self, item_id, sub_id=0):
            self._calls += 1
            if self._calls > 55:
                raise RuntimeError("simulated timeout/crash mid-run")
            return fake_prices.get(item_id)

    with patch("bdo_profit.cli.MarketClient", CrashingClient), \
         patch("bdo_profit.cli.scrape_all", return_value=edges):
        try:
            _run(paths)
        except RuntimeError:
            pass  # the simulated crash -- what matters is what's on disk already

    from bdo_profit.price_cache import load_price_cache

    saved = load_price_cache(paths["price_cache_path"])
    # Some meaningful chunk of the 55 successful fetches must have survived
    # to disk before the crash on call 56 -- not zero, not requiring all 120.
    assert len(saved) >= 40


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
