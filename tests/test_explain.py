from bdo_profit.explain import build_explain
from bdo_profit.models import ConversionEdge, YieldRange

# item1 (raw, price 10) --Make2--> item2 (price 1, not worth selling raw)
# item2 + item10 (side, bought at 50) --Make3--> item3 (price 1000, final sale)
MAKE2 = ConversionEdge(
    recipe_id=1,
    name="Make2",
    process_type="Heating",
    mastery_required=0,
    inputs=((1, 5.0),),
    base_outputs=(YieldRange(2, 2.0, 2.0),),
)
MAKE3 = ConversionEdge(
    recipe_id=2,
    name="Make3",
    process_type="Heating",
    mastery_required=0,
    inputs=((2, 1.0), (10, 3.0)),
    base_outputs=(YieldRange(3, 1.0, 1.0),),
)


# item1 (raw, price 10) --Make2Wide (yield 1-4, avg 2.5)--> item2 (price 1)
# item2 + item10 (side, bought at 50) --Make3Fixed (yield exactly 1)--> item3 (price 1000)
MAKE2_WIDE = ConversionEdge(
    recipe_id=62, name="Make2Wide", process_type="Heating", mastery_required=0,
    inputs=((1, 5.0),), base_outputs=(YieldRange(2, 1.0, 4.0),),
)
MAKE3_FIXED = ConversionEdge(
    recipe_id=63, name="Make3Fixed", process_type="Heating", mastery_required=0,
    inputs=((2, 1.0), (10, 3.0)), base_outputs=(YieldRange(3, 1.0, 1.0),),
)


# item1 (raw, 10) --MakeShard (fixed yield 2)--> item2 (100)
# item2 --MakeIngot (fixed yield 1)--> item3 (1000) -- safe so far, no side cost
# item3 + 2x item10 (side, 500/unit) --MakeCrystal (yield 1-2, avg 1.5)--> item4 (1600)
# The crystal step IS worth taking on average (the forward solver picks it:
# 1.5*1600 - 2*500 = 1400 > item3's raw price of 1000) but its *worst* case
# (1*1600 - 2*500 = 600/unit) is worse than the 2000 already guaranteed by
# stopping one step earlier -- so it should still be excluded from the
# recommended stopping point despite looking attractive on paper.
MAKE_SHARD = ConversionEdge(
    70, "MakeShard", "Heating", 0, ((1, 5.0),), (YieldRange(2, 2.0, 2.0),)
)
MAKE_INGOT = ConversionEdge(
    71, "MakeIngot", "Heating", 0, ((2, 3.0),), (YieldRange(3, 1.0, 1.0),)
)
MAKE_CRYSTAL = ConversionEdge(
    72, "MakeCrystal", "Alchemy", 0, ((3, 1.0), (10, 2.0)), (YieldRange(4, 1.0, 2.0),)
)


def test_build_explain_recommends_stopping_before_a_risky_step():
    prices = {1: 10.0, 2: 100.0, 3: 1000.0, 4: 1600.0, 10: 500.0}
    result = build_explain(
        1, 15.0, [MAKE_SHARD, MAKE_INGOT, MAKE_CRYSTAL], prices, {}, tax_rate=1.0
    )
    # step index 1 = after MakeShard (0) and MakeIngot (1), selling item3 --
    # the best point that's still safe at worst-case yield.
    assert result.recommended_stop_index == 1
    assert result.recommended_stop_value == 2000.0

    points = {p.step_index: p for p in result.stop_points}
    assert points[-1].value_avg == 150.0  # sell item1 raw
    assert points[0].value_avg == 600.0  # sell item2 (after MakeShard)
    assert points[1].value_avg == 2000.0  # sell item3 (after MakeIngot) -- recommended
    assert points[2].value_avg == 2800.0  # push through MakeCrystal -- better on average...
    assert points[2].value_worst == 1200.0  # ...but worst case gives back the guaranteed 2000


def test_build_explain_computes_worst_case_total_using_minimum_yield():
    # Confirmed by the user (Metal Solvent: profitable on average, a real
    # loss at minimum yield) that average-case math alone hides real risk.
    # qty=10 item1 -> 2 batches (fixed, driven by held item1, not yield) ->
    # worst-case item2 = 2 * qty_min(1) = 2 -> 2 batches of Make3Fixed ->
    # worst-case item3 = 2 * 1 = 2, side item10 needed = 2*3=6 @ 50 = 300.
    # worst_case_total = 2*1000 - 300 = 1700 (vs average processed_total,
    # which uses the 2.5 avg yield and comes out higher).
    prices = {1: 10.0, 2: 1.0, 3: 1000.0, 10: 50.0}
    result = build_explain(1, 10.0, [MAKE2_WIDE, MAKE3_FIXED], prices, {}, tax_rate=1.0)
    assert result.worst_case_total == 1700.0
    assert result.worst_case_total < result.processed_total


def test_build_explain_walks_full_chain_with_scaled_quantities():
    prices = {1: 10.0, 2: 1.0, 3: 1000.0, 10: 50.0}
    result = build_explain(1, 10.0, [MAKE2, MAKE3], prices, {}, tax_rate=1.0)

    assert result.item_id == 1
    assert result.qty == 10.0
    assert result.raw_sell_total == 100.0  # 10 units * 10 price * 1.0 tax
    assert result.processed_total == 3400.0  # matches best_path_value's own math

    assert len(result.steps) == 2

    step1 = result.steps[0]
    assert step1.edge.name == "Make2"
    assert step1.primary_input_qty == 10.0  # consumes all 10 item1
    assert step1.batches == 2.0  # 10 / 5 per batch
    assert step1.output_qty_expected == 4.0  # 2 batches * 2 yield
    assert step1.side_ingredients == ()  # no side ingredients on this hop

    step2 = result.steps[1]
    assert step2.edge.name == "Make3"
    assert step2.primary_input_qty == 4.0  # all the item2 produced by step1
    assert step2.batches == 4.0  # 4 item2 / 1 per batch
    assert step2.output_qty_expected == 4.0  # 4 batches * 1 yield
    assert len(step2.side_ingredients) == 1
    plan, total_needed = step2.side_ingredients[0]
    assert plan.item_id == 10
    assert plan.method == "buy_market"
    assert plan.unit_cost == 50.0
    assert total_needed == 12.0  # 4 batches * 3 per batch


def test_build_explain_sell_raw_item_has_no_steps():
    # item1 sells for far more raw than anything downstream is worth.
    prices = {1: 1000.0, 2: 1.0, 3: 1.0, 10: 50.0}
    result = build_explain(1, 5.0, [MAKE2, MAKE3], prices, {}, tax_rate=1.0)
    assert result.steps == ()
    assert result.processed_total == result.raw_sell_total == 5000.0


# A genuine positive-gain cycle (20 <-> 21, 10x each way -- both edges get
# excluded by the forward solver), plus an unrelated item 30 whose recipe
# uses item 20 as a side ingredient. item 20 could ALSO be "crafted" via the
# excluded cycle edge for an artificially cheap 10/unit (1x item21 @ 100 ->
# 10x item20) versus its real market price of 100/unit -- if the exclusion
# from solving item 21 isn't carried into the later call for item 30, this
# cheap-but-bad option would wrongly get recommended.
CYCLE_A = ConversionEdge(20, "A to B", "Grinding", 0, ((20, 1.0),), (YieldRange(21, 10.0, 10.0),))
CYCLE_B = ConversionEdge(21, "B to A", "Grinding", 0, ((21, 1.0),), (YieldRange(20, 10.0, 10.0),))
SIDE_USER = ConversionEdge(
    22, "Make Widget", "Heating", 0, ((30, 1.0), (20, 2.0)), (YieldRange(31, 1.0, 1.0),)
)


# item40 (raw) --Make41From43--> nothing yet; item41 is buyable directly
# (1000/unit, stock 1000) OR craftable from item43 (5x, 10/unit -> 50/unit,
# way cheaper) -- but item43 only has 2 in stock, nowhere near enough for
# the volume this recipe needs. Make42 spends item40 (spine) + item41
# (side) on item42, a valuable final sale.
MAKE41_FROM_43 = ConversionEdge(
    50, "Make41", "Heating", 0, ((43, 5.0),), (YieldRange(41, 1.0, 1.0),)
)
MAKE42 = ConversionEdge(
    51, "Make42", "Heating", 0, ((40, 1.0), (41, 1.0)), (YieldRange(42, 1.0, 1.0),)
)


def test_build_explain_falls_back_when_the_cheaper_craft_path_is_far_understocked():
    prices = {40: 5.0, 41: 1000.0, 42: 100_000.0, 43: 10.0}
    stocks = {41: 1000, 43: 2}
    result = build_explain(
        40, 100.0, [MAKE41_FROM_43, MAKE42], prices, {}, tax_rate=1.0, stocks=stocks
    )
    step = result.steps[0]
    plan, total_needed = step.side_ingredients[0]
    assert plan.item_id == 41
    assert total_needed == 100.0
    # Crafting via item43 would be far cheaper (50/unit vs 1000/unit), but
    # item43's stock (2) is nowhere near the ~500 units that path would
    # need -- must fall back to buying item41 directly instead.
    assert plan.method == "buy_market"
    assert plan.unit_cost == 1000.0


def test_build_explain_shares_excluded_edges_across_calls_with_a_shared_memo():
    prices = {20: 100.0, 21: 100.0, 30: 10.0, 31: 100_000.0}
    edges = [CYCLE_A, CYCLE_B, SIDE_USER]
    memo = {}
    acq_memo = {}
    excluded_edges = set()

    # First call solves the whole graph (including the 20<->21 cycle) and
    # populates the shared memo/excluded_edges.
    build_explain(21, 1.0, edges, prices, {}, tax_rate=1.0, memo=memo, acq_memo=acq_memo, excluded_edges=excluded_edges)
    assert CYCLE_B in excluded_edges

    # Second call, for an unrelated item, reuses that same memo/excluded_edges
    # -- item 30 doesn't trigger a fresh _solve_all (already memoized), so the
    # exclusion must come from the shared state, not a fresh solve.
    result = build_explain(
        30, 5.0, edges, prices, {}, tax_rate=1.0, memo=memo, acq_memo=acq_memo, excluded_edges=excluded_edges
    )
    plan, _ = result.steps[0].side_ingredients[0]
    assert plan.item_id == 20
    assert plan.method == "buy_market"
    assert plan.unit_cost == 100.0
