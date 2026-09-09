from bdo_profit.bottleneck import (
    find_cheapest_farming_action,
    inject_universal_proc_costs,
    universal_proc_farming_cost,
)
from bdo_profit.config import UniversalProc
from bdo_profit.models import ConversionEdge, YieldRange
from bdo_profit.profitability import build_edges_by_output

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


def _eo(edges):
    return build_edges_by_output(edges)


def test_find_cheapest_farming_action_picks_lowest_net_cost_of_matching_type():
    edges = [RECIPE_A, RECIPE_B, RECIPE_C]
    edge, net_cost = find_cheapest_farming_action(
        "Cooking", edges, _eo(edges), PRICES, {}, tax_rate=1.0
    )
    assert edge.name == "Simple Broth"
    assert net_cost == 2.0


def test_find_cheapest_farming_action_allows_negative_net_cost():
    edges = [RECIPE_A, RECIPE_B, RECIPE_D]
    edge, net_cost = find_cheapest_farming_action(
        "Cooking", edges, _eo(edges), PRICES, {}, tax_rate=1.0
    )
    assert edge.name == "Rich Cake"
    assert net_cost == -45.0


def test_find_cheapest_farming_action_skips_unbuyable_ingredients():
    edges = [RECIPE_E, RECIPE_B]
    edge, net_cost = find_cheapest_farming_action(
        "Cooking", edges, _eo(edges), PRICES, {}, tax_rate=1.0
    )
    assert edge.name == "Simple Broth"
    assert net_cost == 2.0


def test_find_cheapest_farming_action_returns_none_when_no_matching_recipes():
    edges = [RECIPE_C]
    assert find_cheapest_farming_action("Cooking", edges, _eo(edges), PRICES, {}, tax_rate=1.0) is None


# Real-world case that surfaced a bug: Lump of Raw Sugar (302) has a market
# price (8,250) that badly overstates its real cost -- it's actually much
# cheaper to craft from NPC-priced Raw Sugar (300, 200/ea) and Mineral Water
# (301, 30/ea) at a 2.5-avg yield: (10*200 + 1*30) / 2.5 = 812/unit. A
# farming-cost search that only looks at flat buy prices for its ingredients
# would badly understate Recipe F's real profitability.
RECIPE_LUMP = ConversionEdge(
    10, "Lump of Raw Sugar", "Heating", 0, ((300, 10.0), (301, 1.0)), (YieldRange(302, 1.0, 4.0),)
)
RECIPE_F = ConversionEdge(
    11, "Uses Lump", "Cooking", 0, ((302, 1.0),), (YieldRange(400, 1.0, 1.0),)
)
RECURSIVE_PRICES = {302: 8250.0, 400: 1000.0}
RECURSIVE_NPC_PRICES = {300: 200.0, 301: 30.0}


def test_find_cheapest_farming_action_uses_recursive_craft_cost_for_ingredients():
    edges = [RECIPE_F, RECIPE_LUMP]
    edge, net_cost = find_cheapest_farming_action(
        "Cooking", edges, _eo(edges), RECURSIVE_PRICES, RECURSIVE_NPC_PRICES, tax_rate=1.0
    )
    assert edge.name == "Uses Lump"
    # 812/unit crafted (not 8,250 bought) - 1000 sale = -188.
    assert net_cost == -188.0


def test_universal_proc_farming_cost_divides_net_cost_by_chance_and_qty():
    edges = [RECIPE_A, RECIPE_B]
    cost = universal_proc_farming_cost(
        "Cooking", chance=0.02, qty_per_proc=1.0,
        edges=edges, edges_by_output=_eo(edges), prices=PRICES, npc_prices={}, tax_rate=1.0,
    )
    # cheapest is Simple Broth at 2.0 net cost/attempt, 1/0.02 = 50 attempts expected.
    assert cost == 100.0


def test_universal_proc_farming_cost_is_free_when_the_dish_is_already_profitable():
    edges = [RECIPE_A, RECIPE_D]
    cost = universal_proc_farming_cost(
        "Cooking", chance=0.02, qty_per_proc=1.0,
        edges=edges, edges_by_output=_eo(edges), prices=PRICES, npc_prices={}, tax_rate=1.0,
    )
    assert cost == 0.0


def test_universal_proc_farming_cost_returns_none_with_no_matching_recipes():
    edges = [RECIPE_C]
    cost = universal_proc_farming_cost(
        "Cooking", chance=0.02, qty_per_proc=1.0,
        edges=edges, edges_by_output=_eo(edges), prices=PRICES, npc_prices={}, tax_rate=1.0,
    )
    assert cost is None


def test_inject_universal_proc_costs_adds_synthetic_npc_price():
    procs = {"Cooking": UniversalProc(item_id=9780, item_name="Witch's Delicacy", chance=0.02, qty=1.0)}
    edges = [RECIPE_A, RECIPE_B]
    updated = inject_universal_proc_costs(
        procs, edges, _eo(edges), PRICES, {}, tax_rate=1.0,
    )
    assert updated[9780] == 100.0  # 2.0 net cost/attempt / 0.02 chance


def test_inject_universal_proc_costs_does_not_mutate_input_dict():
    procs = {"Cooking": UniversalProc(item_id=9780, item_name="Witch's Delicacy", chance=0.02, qty=1.0)}
    edges = [RECIPE_A, RECIPE_B]
    original = {}
    inject_universal_proc_costs(procs, edges, _eo(edges), PRICES, original, tax_rate=1.0)
    assert original == {}


def test_inject_universal_proc_costs_never_overwrites_an_existing_price():
    procs = {"Cooking": UniversalProc(item_id=9780, item_name="Witch's Delicacy", chance=0.02, qty=1.0)}
    edges = [RECIPE_A, RECIPE_B]
    updated = inject_universal_proc_costs(
        procs, edges, _eo(edges), PRICES, {9780: 5.0}, tax_rate=1.0,
    )
    assert updated[9780] == 5.0


def test_inject_universal_proc_costs_skips_unfarmable_proc_items():
    procs = {"Cooking": UniversalProc(item_id=9780, item_name="Witch's Delicacy", chance=0.02, qty=1.0)}
    edges = [RECIPE_C]
    updated = inject_universal_proc_costs(procs, edges, _eo(edges), PRICES, {}, tax_rate=1.0)
    assert 9780 not in updated
