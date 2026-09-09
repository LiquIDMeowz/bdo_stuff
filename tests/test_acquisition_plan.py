from bdo_profit.models import ConversionEdge, YieldRange
from bdo_profit.profitability import (
    build_edges_by_output,
    cheapest_acquisition_plan,
    rank_acquisition_sources,
)

# Toy: item 100 can be bought for 500, or crafted from 2x item 101 (bought at 100 each = 200)
# yielding 2 units per batch -- craft cost = 200/2 = 100/unit, way cheaper than buying at 500.
CRAFT_CHEAPER_RECIPE = ConversionEdge(
    recipe_id=1,
    name="Widget",
    process_type="Heating",
    mastery_required=0,
    inputs=((101, 2.0),),
    base_outputs=(YieldRange(100, 2.0, 2.0),),
)

# Toy: item 200 can be bought for 50, or crafted from 3x item 201 (bought at 100 each = 300)
# yielding 1 unit per batch -- craft cost = 300/1 = 300/unit, way more than buying at 50.
BUY_CHEAPER_RECIPE = ConversionEdge(
    recipe_id=2,
    name="Gadget",
    process_type="Heating",
    mastery_required=0,
    inputs=((201, 3.0),),
    base_outputs=(YieldRange(200, 1.0, 1.0),),
)


def test_build_edges_by_output_indexes_by_produced_item():
    graph = build_edges_by_output([CRAFT_CHEAPER_RECIPE, BUY_CHEAPER_RECIPE])
    assert graph[100] == [CRAFT_CHEAPER_RECIPE]
    assert graph[200] == [BUY_CHEAPER_RECIPE]


def test_prefers_buying_when_cheaper_than_crafting():
    edges_by_output = build_edges_by_output([BUY_CHEAPER_RECIPE])
    prices = {200: 50.0, 201: 100.0}
    memo = {}
    plan = cheapest_acquisition_plan(200, edges_by_output, prices, {}, memo)
    assert plan.method == "buy_market"
    assert plan.unit_cost == 50.0


def test_prefers_crafting_when_cheaper_than_buying():
    # Mirrors the real Brilliant Opal case: buying is 259k, crafting from
    # cheap Rough Opal via Polished Opal is only ~145k -- must recommend craft.
    edges_by_output = build_edges_by_output([CRAFT_CHEAPER_RECIPE])
    prices = {100: 500.0, 101: 100.0}
    memo = {}
    plan = cheapest_acquisition_plan(100, edges_by_output, prices, {}, memo)
    assert plan.method == "craft"
    assert plan.unit_cost == 100.0
    assert plan.recipe_name == "Widget"
    assert plan.yield_expected == 2.0
    assert len(plan.inputs) == 1
    sub_plan, qty = plan.inputs[0]
    assert sub_plan.item_id == 101
    assert qty == 2.0
    assert sub_plan.method == "buy_market"


def test_recursion_goes_multiple_levels_deep():
    # item 1 <- 2x item 2 (yield 2) <- 5x item 3 (yield 5), item 3 bought at 10.
    inner = ConversionEdge(10, "Inner", "Heating", 0, ((3, 5.0),), (YieldRange(2, 5.0, 5.0),))
    outer = ConversionEdge(11, "Outer", "Heating", 0, ((2, 2.0),), (YieldRange(1, 2.0, 2.0),))
    edges_by_output = build_edges_by_output([inner, outer])
    prices = {1: 1_000_000.0, 2: 1_000_000.0, 3: 10.0}
    memo = {}
    plan = cheapest_acquisition_plan(1, edges_by_output, prices, {}, memo)
    assert plan.method == "craft"
    inner_plan, _ = plan.inputs[0]
    assert inner_plan.method == "craft"
    innermost_plan, _ = inner_plan.inputs[0]
    assert innermost_plan.item_id == 3
    assert innermost_plan.method == "buy_market"
    # cost per unit of item 2 = (5 * 10) / 5 = 10; cost per unit of item 1 = (2 * 10) / 2 = 10
    assert plan.unit_cost == 10.0


def test_cycle_falls_back_to_buy_price_without_infinite_recursion():
    # item A's only recipe needs item B, and item B's only recipe needs item A.
    a_from_b = ConversionEdge(20, "A from B", "Heating", 0, ((2, 1.0),), (YieldRange(1, 1.0, 1.0),))
    b_from_a = ConversionEdge(21, "B from A", "Heating", 0, ((1, 1.0),), (YieldRange(2, 1.0, 1.0),))
    edges_by_output = build_edges_by_output([a_from_b, b_from_a])
    prices = {1: 100.0, 2: 200.0}
    memo = {}
    plan = cheapest_acquisition_plan(1, edges_by_output, prices, {}, memo)
    # Must terminate and fall back to a real, bounded price -- not recurse forever.
    assert plan.unit_cost < float("inf")


def test_buy_market_plan_carries_available_stock():
    # Confirmed by the user: current_stock is sell-side listings, not buy
    # orders -- a low/zero stock means "no one's actually selling this",
    # even if a price exists, and buying a large quantity can take days.
    edges_by_output = build_edges_by_output([])
    prices = {300: 1000.0}
    memo = {}
    plan = cheapest_acquisition_plan(300, edges_by_output, prices, {}, memo, stocks={300: 12})
    assert plan.method == "buy_market"
    assert plan.available_stock == 12


def test_zero_stock_item_is_not_offered_as_buyable():
    # Confirmed by the user: an ingredient with zero current sell listings
    # (e.g. Bottle of Sea Water -- trivially self-gatherable, so nobody
    # bothers listing it) shouldn't be recommended as "just buy it" even
    # though a real last-sale price exists -- it's not achievable right
    # now. Falls back to NPC if available, else "unavailable".
    edges_by_output = build_edges_by_output([])
    memo = {}
    plan = cheapest_acquisition_plan(300, edges_by_output, {300: 5550.0}, {}, memo, stocks={300: 0})
    assert plan.method == "unavailable"

    memo2 = {}
    plan2 = cheapest_acquisition_plan(
        301, edges_by_output, {301: 5550.0}, {301: 20.0}, memo2, stocks={301: 0}
    )
    assert plan2.method == "buy_npc"
    assert plan2.unit_cost == 20.0


def test_blocked_market_items_forces_a_fallback():
    # A caller that knows the real quantity needed (this function only ever
    # prices one unit) determines an item is too far short of stock to
    # realistically fill that need, and blocks it -- forcing a fallback to
    # craft or NPC, mirroring the zero-stock case but decided by the caller.
    edges_by_output = build_edges_by_output([])
    memo = {}
    plan = cheapest_acquisition_plan(
        302, edges_by_output, {302: 100.0}, {}, memo, blocked_market_items=frozenset({302}),
    )
    assert plan.method == "unavailable"


def test_excluded_edges_are_skipped():
    # The craft-cheaper recipe would normally win, but if it's a known-bad
    # recipe (already excluded by the forward divergence guard), it must be
    # skipped so we don't recommend crafting via a broken ratio.
    edges_by_output = build_edges_by_output([CRAFT_CHEAPER_RECIPE])
    prices = {100: 500.0, 101: 100.0}
    memo = {}
    plan = cheapest_acquisition_plan(
        100, edges_by_output, prices, {}, memo, excluded_edges=frozenset([CRAFT_CHEAPER_RECIPE])
    )
    assert plan.method == "buy_market"
    assert plan.unit_cost == 500.0


# item 400 has three real ways to get it: buy at 500, craft cheaply from
# item 401 (100/unit), or an NPC price of 300 -- used to test that
# rank_acquisition_sources surfaces ALL of them, sorted, rather than
# silently picking one winner like cheapest_acquisition_plan does.
CRAFT_400_FROM_401 = ConversionEdge(
    10, "Craft400", "Heating", 0, ((401, 2.0),), (YieldRange(400, 2.0, 2.0),)
)
CRAFT_400_EXPENSIVE = ConversionEdge(
    11, "ExpensiveCraft400", "Alchemy", 0, ((402, 1.0),), (YieldRange(400, 1.0, 1.0),)
)


def test_rank_acquisition_sources_lists_every_option_sorted_by_cost():
    edges_by_output = build_edges_by_output([CRAFT_400_FROM_401, CRAFT_400_EXPENSIVE])
    prices = {400: 500.0, 401: 100.0, 402: 900.0}
    npc_prices = {400: 300.0}
    options = rank_acquisition_sources(400, edges_by_output, prices, npc_prices, {})

    assert [o.method for o in options] == ["craft", "buy_npc", "buy_market", "craft"]
    assert options[0].unit_cost == 100.0  # 2x401@100 / 2 yield = 100/unit
    assert options[0].recipe_name == "Craft400"
    assert options[1].unit_cost == 300.0  # NPC
    assert options[2].unit_cost == 500.0  # market
    assert options[3].unit_cost == 900.0  # ExpensiveCraft400: 1x402@900 / 1 yield


def test_rank_acquisition_sources_attaches_stock_to_buy_market():
    edges_by_output = build_edges_by_output([])
    options = rank_acquisition_sources(500, edges_by_output, {500: 1000.0}, {}, {500: 42})
    assert len(options) == 1
    assert options[0].method == "buy_market"
    assert options[0].available_stock == 42


def test_rank_acquisition_sources_skips_excluded_edges():
    edges_by_output = build_edges_by_output([CRAFT_400_FROM_401])
    prices = {400: 500.0, 401: 100.0}
    options = rank_acquisition_sources(
        400, edges_by_output, prices, {}, {}, excluded_edges=frozenset([CRAFT_400_FROM_401])
    )
    assert [o.method for o in options] == ["buy_market"]
