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
