from dataclasses import dataclass

from bdo_profit.models import BonusOutput, ConversionEdge


@dataclass
class PathResult:
    item_id: int
    action: str
    value_per_unit: float
    steps: tuple[str, ...]
    process_types: tuple[str, ...]
    bonus_upside_per_unit: float = 0.0


def build_edges_by_input(edges: list[ConversionEdge]) -> dict[int, list[ConversionEdge]]:
    graph: dict[int, list[ConversionEdge]] = {}
    for edge in edges:
        for item_id, _qty in edge.inputs:
            graph.setdefault(item_id, []).append(edge)
    return graph


def identify_raw_materials(edges: list[ConversionEdge]) -> set[int]:
    all_inputs = {iid for e in edges for iid, _ in e.inputs}
    all_outputs = {yr.item_id for e in edges for yr in e.base_outputs}
    return all_inputs - all_outputs


def all_item_ids(edges: list[ConversionEdge]) -> set[int]:
    ids: set[int] = set()
    for e in edges:
        ids.update(iid for iid, _ in e.inputs)
        ids.update(o.item_id for o in e.base_outputs)
        ids.update(b.item_id for b in e.bonus_outputs)
    return ids


def acquisition_cost(
    item_id: int, prices: dict[int, float], npc_prices: dict[int, float]
) -> float:
    candidates = [
        c for c in (prices.get(item_id), npc_prices.get(item_id)) if c is not None
    ]
    return min(candidates) if candidates else float("inf")


def apply_bonus_rate_overrides(
    edges: list[ConversionEdge], rates: dict[int, float]
) -> list[ConversionEdge]:
    updated = []
    for e in edges:
        rate = rates.get(e.recipe_id)
        if rate is None or not e.bonus_outputs:
            updated.append(e)
            continue
        new_bonus = tuple(
            BonusOutput(b.item_id, b.qty_min, b.qty_max, chance=rate) for b in e.bonus_outputs
        )
        updated.append(
            ConversionEdge(
                e.recipe_id,
                e.name,
                e.process_type,
                e.mastery_required,
                e.inputs,
                e.base_outputs,
                new_bonus,
            )
        )
    return updated


def best_path_value(
    item_id: int,
    edges_by_input: dict[int, list[ConversionEdge]],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    memo: dict[int, PathResult],
    visiting: frozenset[int] = frozenset(),
) -> PathResult:
    """Best value obtainable from one unit of ``item_id``.

    ``visiting`` is accepted for backwards compatibility with the old recursive
    implementation and is unused: the fixed-point solver below is inherently
    cycle-safe, so no in-progress set is needed.

    The whole graph is solved at once on the first call and cached in ``memo``;
    subsequent calls sharing that ``memo`` are dictionary lookups.
    """
    if item_id not in memo:
        _solve_all(edges_by_input, prices, npc_prices, tax_rate, memo)
    if item_id in memo:
        return memo[item_id]
    # Item is not part of the conversion graph at all -- only option is a raw sale.
    sell_value = prices.get(item_id, 0.0) * tax_rate
    return PathResult(item_id, "sell_raw", sell_value, (), (), 0.0)


def _solve_all(
    edges_by_input: dict[int, list[ConversionEdge]],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    memo: dict[int, PathResult],
    max_iters: int = 50,
    epsilon: float = 1e-6,
) -> None:
    """Gauss-Seidel fixed-point relaxation over the whole conversion graph.

    Values start at each item's raw sale value and are relaxed upward in sweeps
    until nothing changes. Cycles converge naturally instead of recursing
    forever, and every item is solved in a single pass over the graph.
    """
    all_edges: set[ConversionEdge] = {
        edge for edges in edges_by_input.values() for edge in edges
    }
    universe: set[int] = set(edges_by_input.keys())
    for edge in all_edges:
        universe.update(iid for iid, _ in edge.inputs)
        universe.update(o.item_id for o in edge.base_outputs)
        universe.update(b.item_id for b in edge.bonus_outputs)

    value: dict[int, float] = {iid: prices.get(iid, 0.0) * tax_rate for iid in universe}
    action: dict[int, str] = {iid: "sell_raw" for iid in universe}
    steps: dict[int, tuple[str, ...]] = {iid: () for iid in universe}
    process_types: dict[int, tuple[str, ...]] = {iid: () for iid in universe}
    bonus_upside: dict[int, float] = {iid: 0.0 for iid in universe}

    for _ in range(max_iters):
        changed = False
        for iid in universe:
            sell_value = prices.get(iid, 0.0) * tax_rate
            best_value, best_action, best_steps, best_types, best_upside = (
                sell_value,
                "sell_raw",
                (),
                (),
                0.0,
            )
            for edge in edges_by_input.get(iid, []):
                candidate = _evaluate_edge_relaxed(
                    edge, iid, value, action, steps, process_types, prices, npc_prices
                )
                if candidate is not None and candidate[0] > best_value:
                    best_value, best_action, best_steps, best_types, best_upside = candidate
            if abs(best_value - value[iid]) > epsilon:
                changed = True
            value[iid] = best_value
            action[iid] = best_action
            steps[iid] = best_steps
            process_types[iid] = best_types
            bonus_upside[iid] = best_upside
        if not changed:
            break
    else:
        print(
            f"WARNING: profitability estimates did not fully converge after "
            f"{max_iters} iterations; values may be approximate"
        )

    for iid in universe:
        memo[iid] = PathResult(
            item_id=iid,
            action=action[iid],
            value_per_unit=value[iid],
            steps=steps[iid],
            process_types=process_types[iid],
            bonus_upside_per_unit=bonus_upside[iid],
        )


def _evaluate_edge_relaxed(
    edge: ConversionEdge,
    target_item_id: int,
    value: dict[int, float],
    action: dict[int, str],
    steps: dict[int, tuple[str, ...]],
    process_types: dict[int, tuple[str, ...]],
    prices: dict[int, float],
    npc_prices: dict[int, float],
) -> tuple[float, str, tuple[str, ...], tuple[str, ...], float] | None:
    target_qty = next((qty for iid, qty in edge.inputs if iid == target_item_id), None)
    if not target_qty:
        return None

    other_cost = sum(
        acquisition_cost(iid, prices, npc_prices) * qty
        for iid, qty in edge.inputs
        if iid != target_item_id
    )

    output_value = 0.0
    downstream_steps: tuple[str, ...] = ()
    downstream_types: tuple[str, ...] = ()
    for i, out in enumerate(edge.base_outputs):
        out_value = value.get(out.item_id, 0.0)
        output_value += out.expected_qty * out_value
        if i == 0 and action.get(out.item_id) != "sell_raw":
            downstream_steps = steps.get(out.item_id, ())
            downstream_types = process_types.get(out.item_id, ())

    known_bonus_value = 0.0
    unknown_bonus_upside = 0.0
    for bonus in edge.bonus_outputs:
        bonus_value = value.get(bonus.item_id, 0.0)
        if bonus.chance is None:
            unknown_bonus_upside += bonus.expected_qty * bonus_value
        else:
            known_bonus_value += bonus.expected_qty * bonus.chance * bonus_value

    net_per_batch = output_value - other_cost + known_bonus_value
    value_per_unit = net_per_batch / target_qty
    bonus_upside_per_unit = unknown_bonus_upside / target_qty

    action_str = f"{edge.process_type}: {edge.name}"
    if downstream_steps:
        action_str += " -> " + " -> ".join(downstream_steps)

    return (
        value_per_unit,
        action_str,
        (edge.name,) + downstream_steps,
        (edge.process_type,) + downstream_types,
        bonus_upside_per_unit,
    )
