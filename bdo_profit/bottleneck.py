from bdo_profit.config import UniversalProc
from bdo_profit.models import ConversionEdge
from bdo_profit.profitability import acquisition_cost


def find_cheapest_farming_action(
    process_type: str,
    edges: list[ConversionEdge],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    stocks: dict[int, float] | None = None,
    excluded_edges: frozenset[ConversionEdge] = frozenset(),
) -> tuple[ConversionEdge, float] | None:
    """Cheapest recipe of ``process_type`` to run purely to trigger a
    universal per-action proc (e.g. Cooking's ~2% Witch's Delicacy chance)
    -- not to sell its own output.

    "Cheapest" is net cost per attempt: ingredient cost minus the value
    recovered by also selling the recipe's own output (taxed). A negative
    net cost means the recipe is worth running outright, so farming the
    proc through it is free (or better).
    """
    best: tuple[ConversionEdge, float] | None = None
    for edge in edges:
        if edge.process_type != process_type or edge in excluded_edges:
            continue
        input_cost = sum(
            acquisition_cost(iid, prices, npc_prices, stocks) * qty
            for iid, qty in edge.inputs
        )
        if input_cost == float("inf"):
            continue
        output_value = sum(
            out.expected_qty * prices.get(out.item_id, 0.0) * tax_rate
            for out in edge.base_outputs
        )
        net_cost = input_cost - output_value
        if best is None or net_cost < best[1]:
            best = (edge, net_cost)
    return best


def universal_proc_farming_cost(
    process_type: str,
    chance: float,
    qty_per_proc: float,
    edges: list[ConversionEdge],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    stocks: dict[int, float] | None = None,
    excluded_edges: frozenset[ConversionEdge] = frozenset(),
) -> float | None:
    """Expected silver cost per unit of a universal-proc item, farmed by
    repeating the cheapest matching-``process_type`` recipe until it procs.

    Returns ``None`` when no recipe of that process_type is farmable at all.
    """
    found = find_cheapest_farming_action(
        process_type, edges, prices, npc_prices, tax_rate, stocks, excluded_edges
    )
    if found is None:
        return None
    _edge, net_cost = found
    if net_cost <= 0:
        return 0.0
    expected_attempts = 1.0 / chance
    return (net_cost * expected_attempts) / qty_per_proc


def inject_universal_proc_costs(
    universal_procs: dict[str, UniversalProc],
    edges: list[ConversionEdge],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    stocks: dict[int, float] | None = None,
    excluded_edges: frozenset[ConversionEdge] = frozenset(),
) -> dict[int, float]:
    """Return a copy of ``npc_prices`` with a synthetic acquisition cost
    added for each universal-proc item (e.g. Witch's Delicacy), computed by
    farming its cheapest matching recipe.

    This lets the existing acquisition-cost machinery (``acquisition_cost``,
    ``cheapest_acquisition_plan``, ``rank_acquisition_sources``) treat a
    universal-proc item as just another buyable item with a real cost,
    instead of needing bespoke handling everywhere it might show up as an
    ingredient (e.g. an NPC exchange recipe that consumes it).

    Never overwrites an item_id already present in ``npc_prices`` -- a
    hand-configured real price always wins over a computed one.
    """
    updated = dict(npc_prices)
    for process_type, proc in universal_procs.items():
        if proc.item_id in updated:
            continue
        cost = universal_proc_farming_cost(
            process_type, proc.chance, proc.qty, edges, prices, npc_prices,
            tax_rate, stocks, excluded_edges,
        )
        if cost is not None:
            updated[proc.item_id] = cost
    return updated
