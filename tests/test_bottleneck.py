from bdo_profit.bottleneck import (
    find_cheapest_farming_action,
    inject_universal_proc_costs,
    universal_proc_farming_cost,
)
from bdo_profit.config import UniversalProc
from bdo_profit.models import ConversionEdge, YieldRange

# Two Cooking recipes and one Alchemy recipe, all farmable purely for a
# universal per-action proc (not for their own output's value).
# Recipe A: 2x item100 (10/ea = 20) -> 1x item200 (sells 5) -> net cost 15.
RECIPE_A = ConversionEdge(
    1, "Cheap Stew", "Cooking", 0, ((100, 2.0),), (YieldRange(200, 1.0, 1.0),)
)
# Recipe B: 1x item101 (5) -> 1x item201 (sells 3) -> net cost 2 (cheapest Cooking).
RECIPE_B = ConversionEdge(
    2, "Simple Broth", "Cooking", 0, ((101, 1.0),), (YieldRange(201, 1.0, 1.0),)
)
# Recipe C: same cheap input, but Alchemy -- must not be picked for a Cooking search.
RECIPE_C = ConversionEdge(
    3, "Simple Tonic", "Alchemy", 0, ((101, 1.0),), (YieldRange(202, 1.0, 100.0),)
)
# Recipe D: 1x item101 (5) -> 1x item203 (sells 50) -> net cost -45 (profitable outright).
RECIPE_D = ConversionEdge(
    4, "Rich Cake", "Cooking", 0, ((101, 1.0),), (YieldRange(203, 1.0, 1.0),)
)
# Recipe E: needs an ingredient nobody sells -- must be skipped, not crash.
RECIPE_E = ConversionEdge(
    5, "Impossible Dish", "Cooking", 0, ((999, 1.0),), (YieldRange(204, 1.0, 1.0),)
)

PRICES = {100: 10.0, 101: 5.0, 200: 5.0, 201: 3.0, 202: 100.0, 203: 50.0}


def test_find_cheapest_farming_action_picks_lowest_net_cost_of_matching_type():
    edge, net_cost = find_cheapest_farming_action(
        "Cooking", [RECIPE_A, RECIPE_B, RECIPE_C], PRICES, {}, tax_rate=1.0
    )
    assert edge.name == "Simple Broth"
    assert net_cost == 2.0


def test_find_cheapest_farming_action_allows_negative_net_cost():
    edge, net_cost = find_cheapest_farming_action(
        "Cooking", [RECIPE_A, RECIPE_B, RECIPE_D], PRICES, {}, tax_rate=1.0
    )
    assert edge.name == "Rich Cake"
    assert net_cost == -45.0


def test_find_cheapest_farming_action_skips_unbuyable_ingredients():
    edge, net_cost = find_cheapest_farming_action(
        "Cooking", [RECIPE_E, RECIPE_B], PRICES, {}, tax_rate=1.0
    )
    assert edge.name == "Simple Broth"
    assert net_cost == 2.0


def test_find_cheapest_farming_action_returns_none_when_no_matching_recipes():
    assert find_cheapest_farming_action("Cooking", [RECIPE_C], PRICES, {}, tax_rate=1.0) is None


def test_universal_proc_farming_cost_divides_net_cost_by_chance_and_qty():
    cost = universal_proc_farming_cost(
        "Cooking", chance=0.02, qty_per_proc=1.0,
        edges=[RECIPE_A, RECIPE_B], prices=PRICES, npc_prices={}, tax_rate=1.0,
    )
    # cheapest is Simple Broth at 2.0 net cost/attempt, 1/0.02 = 50 attempts expected.
    assert cost == 100.0


def test_universal_proc_farming_cost_is_free_when_the_dish_is_already_profitable():
    cost = universal_proc_farming_cost(
        "Cooking", chance=0.02, qty_per_proc=1.0,
        edges=[RECIPE_A, RECIPE_D], prices=PRICES, npc_prices={}, tax_rate=1.0,
    )
    assert cost == 0.0


def test_universal_proc_farming_cost_returns_none_with_no_matching_recipes():
    cost = universal_proc_farming_cost(
        "Cooking", chance=0.02, qty_per_proc=1.0,
        edges=[RECIPE_C], prices=PRICES, npc_prices={}, tax_rate=1.0,
    )
    assert cost is None


def test_inject_universal_proc_costs_adds_synthetic_npc_price():
    procs = {"Cooking": UniversalProc(item_id=9780, item_name="Witch's Delicacy", chance=0.02, qty=1.0)}
    updated = inject_universal_proc_costs(
        procs, [RECIPE_A, RECIPE_B], PRICES, {}, tax_rate=1.0,
    )
    assert updated[9780] == 100.0  # 2.0 net cost/attempt / 0.02 chance


def test_inject_universal_proc_costs_does_not_mutate_input_dict():
    procs = {"Cooking": UniversalProc(item_id=9780, item_name="Witch's Delicacy", chance=0.02, qty=1.0)}
    original = {}
    inject_universal_proc_costs(procs, [RECIPE_A, RECIPE_B], PRICES, original, tax_rate=1.0)
    assert original == {}


def test_inject_universal_proc_costs_never_overwrites_an_existing_price():
    procs = {"Cooking": UniversalProc(item_id=9780, item_name="Witch's Delicacy", chance=0.02, qty=1.0)}
    updated = inject_universal_proc_costs(
        procs, [RECIPE_A, RECIPE_B], PRICES, {9780: 5.0}, tax_rate=1.0,
    )
    assert updated[9780] == 5.0


def test_inject_universal_proc_costs_skips_unfarmable_proc_items():
    procs = {"Cooking": UniversalProc(item_id=9780, item_name="Witch's Delicacy", chance=0.02, qty=1.0)}
    updated = inject_universal_proc_costs(procs, [RECIPE_C], PRICES, {}, tax_rate=1.0)
    assert 9780 not in updated
