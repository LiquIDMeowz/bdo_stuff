from bdo_profit.models import BonusOutput, ConversionEdge, YieldRange
from bdo_profit.profitability import (
    acquisition_cost,
    all_item_ids,
    apply_bonus_rate_overrides,
    best_path_value,
    build_edges_by_input,
    identify_raw_materials,
)

# Toy chain: raw Log (1) --Chopping--> Plank (2) --Chopping--> Square Timber (3)
# Plank chopping also has an unknown-rate bonus output of Square Timber (tier-skip).
LOG_TO_PLANK = ConversionEdge(
    recipe_id=1,
    name="Plank",
    process_type="Chopping",
    mastery_required=0,
    inputs=((1, 10.0),),
    base_outputs=(YieldRange(2, 8.0, 8.0),),
    bonus_outputs=(BonusOutput(3, 1.0, 1.0, chance=None),),
)
PLANK_TO_TIMBER = ConversionEdge(
    recipe_id=2,
    name="Square Timber",
    process_type="Chopping",
    mastery_required=0,
    inputs=((2, 5.0),),
    base_outputs=(YieldRange(3, 1.0, 1.0),),
)


def test_identify_raw_materials():
    raw = identify_raw_materials([LOG_TO_PLANK, PLANK_TO_TIMBER])
    assert raw == {1}


def test_all_item_ids():
    ids = all_item_ids([LOG_TO_PLANK, PLANK_TO_TIMBER])
    assert ids == {1, 2, 3}


def test_acquisition_cost_prefers_cheaper_of_market_and_npc():
    prices = {5000: 20.0}
    npc = {5000: 14.0}
    assert acquisition_cost(5000, prices, npc) == 14.0


def test_acquisition_cost_falls_back_to_whichever_exists():
    assert acquisition_cost(1, {1: 100.0}, {}) == 100.0
    assert acquisition_cost(1, {}, {1: 50.0}) == 50.0


def test_sell_raw_when_no_processing_beats_selling():
    edges_by_input = build_edges_by_input([])
    prices = {1: 100.0}
    result = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    assert result.action == "sell_raw"
    assert result.value_per_unit == 65.0


def test_prefers_processing_when_it_pays_more_than_raw_sale():
    edges_by_input = build_edges_by_input([LOG_TO_PLANK, PLANK_TO_TIMBER])
    # Log sells for 10, Plank sells for 200, Timber sells for 5000.
    # Processing: 10 logs -> 8 planks -> 1.6 timber @ 3250/unit (after tax) = 5200 per batch
    # Value per log: 5200 / 10 = 520/unit (core), plus unknown bonus upside.
    prices = {1: 10.0, 2: 200.0, 3: 5000.0}
    result = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    assert result.action != "sell_raw"
    assert result.value_per_unit == 520.0


def test_prefers_raw_sale_when_processing_loses_value():
    edges_by_input = build_edges_by_input([LOG_TO_PLANK, PLANK_TO_TIMBER])
    # Log sells for 1000, Plank/Timber are nearly worthless -> raw sale wins.
    prices = {1: 1000.0, 2: 1.0, 3: 1.0}
    result = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    assert result.action == "sell_raw"
    assert result.value_per_unit == 650.0


def test_unknown_bonus_output_tracked_as_upside_not_core_value():
    edges_by_input = build_edges_by_input([LOG_TO_PLANK, PLANK_TO_TIMBER])
    prices = {1: 10.0, 2: 200.0, 3: 5000.0}
    without_bonus = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    # Bonus is 1 unknown-chance timber per 10 logs @ 3250/unit = 325/unit upside
    assert without_bonus.value_per_unit == 520.0
    assert without_bonus.bonus_upside_per_unit == 325.0


def test_apply_bonus_rate_overrides_sets_known_chance():
    updated = apply_bonus_rate_overrides([LOG_TO_PLANK], {1: 0.1})
    assert updated[0].bonus_outputs[0].chance == 0.1
    # original object is untouched (edges are frozen/immutable)
    assert LOG_TO_PLANK.bonus_outputs[0].chance is None


def test_multi_input_edge_uses_acquisition_cost_for_secondary_ingredients():
    # Cooking-style recipe: target item (1) + a bought ingredient (99) -> dish (2)
    recipe = ConversionEdge(
        recipe_id=9,
        name="Dish",
        process_type="Cooking",
        mastery_required=0,
        inputs=((1, 1.0), (99, 2.0)),
        base_outputs=(YieldRange(2, 1.0, 1.0),),
    )
    edges_by_input = build_edges_by_input([recipe])
    prices = {1: 10.0, 2: 1000.0, 99: 50.0}
    result = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    # net = (1000*0.65) - (50*2) = 550; value_per_unit = 550 / 1 (qty of item 1 in recipe)
    assert result.value_per_unit == 550.0


def test_known_chance_bonus_included_in_core_value():
    # Apply override to set a known 0.1 chance on the LOG_TO_PLANK bonus
    updated_edges = apply_bonus_rate_overrides([LOG_TO_PLANK, PLANK_TO_TIMBER], {1: 0.1})
    edges_by_input = build_edges_by_input(updated_edges)
    prices = {1: 10.0, 2: 200.0, 3: 5000.0}
    result = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    # Core value: 520.0 (from plank processing)
    # Known bonus: 1.0 timber * 0.1 chance * 3250 value / 10 logs = 32.5
    # Total: 520.0 + 32.5 = 552.5
    # Upside: 0.0 (bonus now has known chance, not unknown)
    assert result.value_per_unit == 552.5
    assert result.bonus_upside_per_unit == 0.0


def test_cyclic_graph_terminates_without_infinite_recursion():
    # Create a 2-cycle: A -> B, B -> A
    edge_a_to_b = ConversionEdge(
        recipe_id=10,
        name="B from A",
        process_type="Processing",
        mastery_required=0,
        inputs=((100, 1.0),),
        base_outputs=(YieldRange(101, 1.0, 1.0),),
    )
    edge_b_to_a = ConversionEdge(
        recipe_id=11,
        name="A from B",
        process_type="Processing",
        mastery_required=0,
        inputs=((101, 1.0),),
        base_outputs=(YieldRange(100, 1.0, 1.0),),
    )
    edges_by_input = build_edges_by_input([edge_a_to_b, edge_b_to_a])
    prices = {100: 10.0, 101: 20.0}

    # Call from item A (100) — should not infinite loop
    # A sells raw for 6.5, but can process to B (sells for 13.0), so chooses processing
    result_a = best_path_value(100, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    assert result_a is not None
    assert result_a.action == "Processing: B from A"
    assert result_a.value_per_unit == 13.0  # 20.0 * 0.65 (B's sell value)

    # Call from item B (101) with a fresh memo — should choose its best path too
    # B sells raw for 13.0, processing to A only yields 6.5, so chooses raw
    result_b = best_path_value(101, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    assert result_b is not None
    assert result_b.action == "sell_raw"
    assert result_b.value_per_unit == 13.0  # 20.0 * 0.65
