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


def category_reachable_items(
    edges: list[ConversionEdge],
    category_process_types: set[str],
    max_hops: int | None = 2,
) -> set[int]:
    """Every item connected, within ``max_hops`` edges, to a matching-category edge.

    Structural only -- no prices involved, so this is stable regardless of what
    turns out to be the "best" chain once real prices are known. Used to scope
    price-fetching to a `--category` filter instead of pricing the whole graph.

    ``max_hops=None`` means unbounded (the full connected component). In
    practice a handful of common staple ingredients (Water, Squid, Corn
    Dough...) sit in a huge number of unrelated recipes, so unbounded closure
    tends to pull in most of the graph regardless of category -- a small hop
    bound keeps this scoping actually useful while still covering realistic
    multi-hop chains within the requested category.
    """
    adjacency: dict[int, set[int]] = {}
    for edge in edges:
        members = (
            [iid for iid, _ in edge.inputs]
            + [o.item_id for o in edge.base_outputs]
            + [b.item_id for b in edge.bonus_outputs]
        )
        for a in members:
            for b in members:
                if a != b:
                    adjacency.setdefault(a, set()).add(b)

    seed: set[int] = set()
    for edge in edges:
        if edge.process_type in category_process_types:
            seed.update(iid for iid, _ in edge.inputs)
            seed.update(o.item_id for o in edge.base_outputs)
            seed.update(b.item_id for b in edge.bonus_outputs)

    visited = set(seed)
    frontier = list(seed)
    hops = 0
    while frontier and (max_hops is None or hops < max_hops):
        hops += 1
        next_frontier = []
        for item in frontier:
            for neighbor in adjacency.get(item, ()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return visited


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


MAX_PLAUSIBLE_VALUE = 1e15  # comfortably above any real BDO price, catches divergence


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

    Not every cycle has a fixed point, though: bad recipe data (e.g. a
    quantity/ratio error) can create a genuine positive-gain loop, where each
    trip around nets more value than it took, and no amount of relaxation
    converges -- it grows every sweep until max_iters, similarly for both a
    slow crawl toward a merely-wrong number and a fast blowup toward an
    astronomical, meaningless one. Items still changing by more than
    ``epsilon`` when max_iters is exhausted, or whose value exceeds
    ``MAX_PLAUSIBLE_VALUE``, are treated as unreliable and reset to their
    plain raw-sale value rather than surfaced as a real number.
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

    still_changing: set[int] = set()
    for _ in range(max_iters):
        changed = False
        still_changing = set()
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
                still_changing.add(iid)
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
            f"{max_iters} iterations; {len(still_changing)} item(s) still changing"
        )

    unreliable = still_changing | {
        iid for iid in universe if abs(value[iid]) > MAX_PLAUSIBLE_VALUE
    }
    if unreliable:
        sample = sorted(unreliable)[:10]
        more = f" (+{len(unreliable) - 10} more)" if len(unreliable) > 10 else ""
        print(
            f"WARNING: {len(unreliable)} item(s) produced an unreliable/diverging value "
            f"(likely a recipe data issue, e.g. a bad quantity ratio creating a "
            f"positive-gain cycle) -- reset to plain raw-sale value: {sample}{more}"
        )

    for iid in universe:
        if iid in unreliable:
            memo[iid] = PathResult(
                item_id=iid,
                action="sell_raw",
                value_per_unit=prices.get(iid, 0.0) * tax_rate,
                steps=(),
                process_types=(),
                bonus_upside_per_unit=0.0,
            )
        else:
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
