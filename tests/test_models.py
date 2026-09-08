from bdo_profit.models import YieldRange, BonusOutput, ConversionEdge


def test_yield_range_expected_qty_is_midpoint():
    yr = YieldRange(item_id=4051, qty_min=1, qty_max=4)
    assert yr.expected_qty == 2.5


def test_bonus_output_expected_qty_is_midpoint():
    bo = BonusOutput(item_id=4052, qty_min=1, qty_max=1, chance=None)
    assert bo.expected_qty == 1.0


def test_conversion_edge_defaults_no_bonus():
    edge = ConversionEdge(
        recipe_id=1,
        name="Melted Iron Shard",
        process_type="Heating",
        mastery_required=0,
        inputs=((4001, 5.0),),
        base_outputs=(YieldRange(4051, 1, 4),),
    )
    assert edge.bonus_outputs == ()
    assert edge.inputs == ((4001, 5.0),)
