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
    if item_id in memo and not visiting:
        return memo[item_id]

    sell_value = prices.get(item_id, 0.0) * tax_rate
    best = PathResult(item_id, "sell_raw", sell_value, (), ())

    if item_id not in visiting:
        next_visiting = visiting | {item_id}
        for edge in edges_by_input.get(item_id, []):
            candidate = _evaluate_edge(
                edge, item_id, edges_by_input, prices, npc_prices, tax_rate, memo, next_visiting
            )
            if candidate is not None and candidate.value_per_unit > best.value_per_unit:
                best = candidate

    if not visiting:
        memo[item_id] = best
    return best


def _evaluate_edge(
    edge: ConversionEdge,
    target_item_id: int,
    edges_by_input: dict[int, list[ConversionEdge]],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    memo: dict[int, PathResult],
    visiting: frozenset[int],
) -> PathResult | None:
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
        downstream = best_path_value(
            out.item_id, edges_by_input, prices, npc_prices, tax_rate, memo, visiting
        )
        output_value += out.expected_qty * downstream.value_per_unit
        if i == 0 and downstream.action != "sell_raw":
            downstream_steps = downstream.steps
            downstream_types = downstream.process_types

    known_bonus_value = 0.0
    unknown_bonus_upside = 0.0
    for bonus in edge.bonus_outputs:
        bonus_unit_value = best_path_value(
            bonus.item_id, edges_by_input, prices, npc_prices, tax_rate, memo, visiting
        ).value_per_unit
        if bonus.chance is None:
            unknown_bonus_upside += bonus.expected_qty * bonus_unit_value
        else:
            known_bonus_value += bonus.expected_qty * bonus.chance * bonus_unit_value

    net_per_batch = output_value - other_cost + known_bonus_value
    value_per_unit = net_per_batch / target_qty
    bonus_upside_per_unit = unknown_bonus_upside / target_qty

    action = f"{edge.process_type}: {edge.name}"
    if downstream_steps:
        action += " -> " + " -> ".join(downstream_steps)

    return PathResult(
        item_id=target_item_id,
        action=action,
        value_per_unit=value_per_unit,
        steps=(edge.name,) + downstream_steps,
        process_types=(edge.process_type,) + downstream_types,
        bonus_upside_per_unit=bonus_upside_per_unit,
    )
