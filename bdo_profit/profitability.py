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
    edge_chain: tuple[ConversionEdge, ...] = ()


def build_edges_by_input(edges: list[ConversionEdge]) -> dict[int, list[ConversionEdge]]:
    graph: dict[int, list[ConversionEdge]] = {}
    for edge in edges:
        for item_id, _qty in edge.inputs:
            graph.setdefault(item_id, []).append(edge)
    return graph


def build_edges_by_output(edges: list[ConversionEdge]) -> dict[int, list[ConversionEdge]]:
    graph: dict[int, list[ConversionEdge]] = {}
    for edge in edges:
        for out in edge.base_outputs:
            graph.setdefault(out.item_id, []).append(edge)
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
    item_id: int,
    prices: dict[int, float],
    npc_prices: dict[int, float],
    stocks: dict[int, float] | None = None,
) -> float:
    """Cheapest way to acquire one unit of ``item_id``, market or NPC.

    A market price of exactly 0 means the item currently has no active
    sell listings (confirmed on real data), not that it's free -- treating
    it as a real, cheaper-than-NPC price silently inflated any recipe that
    happened to use an out-of-stock ingredient. NPC prices are hand-entered
    real purchase costs, so they aren't filtered the same way.

    ``stocks``, when given, applies the same logic to a real but stale
    price: confirmed by the user that an item can carry a genuine nonzero
    last-sale price while currently having zero sell listings (e.g. Bottle
    of Sea Water -- trivially self-gatherable, so nobody bothers listing
    it) -- relying on "just buy it" is unrealistic there even though a
    price exists. Missing stock data for an item (not fetched) doesn't
    penalize it -- only a confirmed zero does. NPC vendors have
    effectively infinite stock by design, so an NPC price is unaffected.
    """
    market_price = prices.get(item_id)
    if stocks is not None and stocks.get(item_id) == 0:
        market_price = None
    candidates = [
        c
        for c in (market_price if market_price else None, npc_prices.get(item_id))
        if c is not None
    ]
    return min(candidates) if candidates else float("inf")


@dataclass
class AcquisitionPlan:
    """Cheapest way to get one unit of an item: buy it, or craft it from its
    own cheapest-acquired ingredients, recursively."""

    item_id: int
    unit_cost: float
    method: str  # "buy_market", "buy_npc", "craft", or "unavailable"
    recipe_name: str | None = None
    process_type: str | None = None
    recipe_id: int | None = None
    yield_expected: float | None = None
    inputs: tuple[tuple["AcquisitionPlan", float], ...] = ()
    available_stock: float | None = None  # current sell-side listings, for buy_market only


def cheapest_acquisition_plan(
    item_id: int,
    edges_by_output: dict[int, list[ConversionEdge]],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    memo: dict[int, AcquisitionPlan],
    excluded_edges: frozenset[ConversionEdge] = frozenset(),
    visiting: frozenset[int] = frozenset(),
    stocks: dict[int, float] = {},
) -> AcquisitionPlan:
    """Cheapest way to acquire one unit of ``item_id``: buy it outright, or
    craft it from its own cheapest-acquired inputs (recursively).

    This is the mirror image of ``best_path_value``: that finds the best
    value from processing an item *forward* toward a sale; this finds the
    cheapest way to *create* an item from scratch. A recipe with a wrong,
    too-generous yield ratio makes crafting look artificially cheap here,
    the same way it makes processing look artificially valuable in the
    forward direction -- so callers should pass the same ``excluded_edges``
    the forward solver already identified as bad, rather than re-deriving
    trust independently.

    A buy-vs-craft cycle (item A's only recipe needs B, B's only recipe
    needs A) is broken by falling back to a plain buy price whenever a
    node is revisited mid-recursion, so this always terminates -- mirrors
    the forward solver falling back to a raw sale on a genuine cycle.

    ``stocks``, when given, is attached to any buy-market plan as
    ``available_stock``. Confirmed by the user: this reflects current
    sell-side listings, not buy orders -- a real price can still exist with
    near-zero stock (nobody currently selling), and a caller recommending a
    large purchase against that needs the stock figure to warn accordingly.
    """
    if item_id in memo:
        return memo[item_id]

    market_price = prices.get(item_id)
    zero_stock = stocks.get(item_id) == 0
    npc_price = npc_prices.get(item_id)
    buy_cost = float("inf")
    buy_method = "unavailable"
    if market_price and not zero_stock:  # a price of 0, or zero current sell
        buy_cost = market_price          # listings, both mean "not really buyable"
        buy_method = "buy_market"
    if npc_price is not None and npc_price < buy_cost:
        buy_cost = npc_price
        buy_method = "buy_npc"

    best_plan = AcquisitionPlan(
        item_id, buy_cost, buy_method,
        available_stock=stocks.get(item_id) if buy_method == "buy_market" else None,
    )

    if item_id in visiting:
        return best_plan

    for edge in edges_by_output.get(item_id, []):
        if edge in excluded_edges:
            continue
        out = next((o for o in edge.base_outputs if o.item_id == item_id), None)
        if out is None or out.expected_qty <= 0:
            continue
        sub_plans: list[tuple[AcquisitionPlan, float]] = []
        total_input_cost = 0.0
        valid = True
        for in_id, in_qty in edge.inputs:
            sub_plan = cheapest_acquisition_plan(
                in_id, edges_by_output, prices, npc_prices, memo, excluded_edges,
                visiting | {item_id}, stocks,
            )
            if sub_plan.unit_cost == float("inf"):
                valid = False
                break
            sub_plans.append((sub_plan, in_qty))
            total_input_cost += sub_plan.unit_cost * in_qty
        if not valid:
            continue
        candidate_cost = total_input_cost / out.expected_qty
        if candidate_cost < best_plan.unit_cost:
            best_plan = AcquisitionPlan(
                item_id,
                candidate_cost,
                "craft",
                recipe_name=edge.name,
                process_type=edge.process_type,
                recipe_id=edge.recipe_id,
                yield_expected=out.expected_qty,
                inputs=tuple(sub_plans),
            )

    memo[item_id] = best_plan
    return best_plan


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
    excluded_edges_out: set[ConversionEdge] | None = None,
    stocks: dict[int, float] | None = None,
) -> PathResult:
    """Best value obtainable from one unit of ``item_id``.

    ``visiting`` is accepted for backwards compatibility with the old recursive
    implementation and is unused: the fixed-point solver below is inherently
    cycle-safe, so no in-progress set is needed.

    The whole graph is solved at once on the first call and cached in ``memo``;
    subsequent calls sharing that ``memo`` are dictionary lookups.

    ``excluded_edges_out``, if given, is populated with whichever recipes the
    divergence guard had to strip out as bad data -- callers that also need
    to reason about the graph in the *other* direction (e.g. cheapest
    acquisition cost) can reuse this instead of re-deriving trust.

    ``stocks``, when given, keeps a zero-stock ingredient from being treated
    as buyable anywhere in the graph (see ``acquisition_cost``) -- confirmed
    by the user this matters for what gets chosen as "best", not just for
    warning about it after the fact: a chain that only pencils out through
    an ingredient nobody is currently selling should lose to a shorter,
    actually-achievable one.
    """
    if item_id not in memo:
        _solve_all(
            edges_by_input, prices, npc_prices, tax_rate, memo,
            excluded_edges_out=excluded_edges_out, stocks=stocks,
        )
    if item_id in memo:
        return memo[item_id]
    # Item is not part of the conversion graph at all -- only option is a raw sale.
    sell_value = prices.get(item_id, 0.0) * tax_rate
    return PathResult(item_id, "sell_raw", sell_value, (), (), 0.0)


MAX_PLAUSIBLE_VALUE = 1e11  # there's no universal Central Market cap -- rare boss-drop
# items (Khan's Heart, Vell's Heart, Kabua's Artifact) genuinely trade up to ~15-20B
# silver, confirmed against real listings. This stays comfortably above any real price
# while still catching genuine exploit-cycle blowups, which reach 1e20+ in practice.
MAX_DIVERGENCE_RETRIES = 100  # each retry strips out one more (usually small) cycle


def _solve_all(
    edges_by_input: dict[int, list[ConversionEdge]],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    memo: dict[int, PathResult],
    max_iters: int = 50,
    epsilon: float = 1e-6,
    excluded_edges_out: set[ConversionEdge] | None = None,
    stocks: dict[int, float] | None = None,
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
    astronomical, meaningless one.

    When that happens, this does NOT just blanket-distrust the whole
    (possibly huge, shared) connected component that the diverging items sit
    in -- confirmed on real data that this can sweep in hundreds of entirely
    unrelated, perfectly stable items (e.g. basic ore smelting) purely
    because the graph is large and interconnected. Instead: identify exactly
    which edge each still-diverging item was using when the sweep budget ran
    out, strip only those specific edges out, and re-solve from scratch.

    Some positive-gain cycles settle into a self-consistent but still
    astronomically large fixed point *within* the sweep budget, rather than
    visibly oscillating -- so they never show up in ``still_changing`` at
    all. Left alone, that implausible-but-"stable" value gets picked up by
    any unrelated legitimate recipe that happens to consume the poisoned
    item (confirmed on real data: basic ore smelting inherited a ~1e22
    value this way, through a multi-hop boss-crystal chain it has nothing
    else to do with). So every item whose value exceeds
    ``MAX_PLAUSIBLE_VALUE`` is fed into the same cycle-detection walk as the
    oscillating items: only the edges that form an actual cycle *within*
    that combined set get excluded. An item that merely consumes something
    implausible without being part of the loop itself (e.g. Melted Iron
    Shard, which is on the path *into* the crystal economy but never part
    of its loop) is correctly left alone, so its edge is never blamed --
    confirmed on real data this was previously excluding "Heating: Melted
    Iron Shard" itself and permanently sinking Iron Ore to sell_raw.

    Not every implausible value comes from a cycle, though -- confirmed on
    real data: a single recipe with a wildly wrong ratio (1 input -> 720
    output units) sitting partway down an otherwise-ordinary acyclic chain
    inflates everything upstream of it without ever repeating a node. Cycle
    detection alone can't see that (nothing repeats), so the same walk also
    looks for the exact point where a chain of implausible values bottoms
    out at a plausible one -- that transition edge is the true origin and
    is excluded on its own, leaving the plausible edges on both sides of it
    untouched.

    Repeat until everything converges to plausible values or the retry
    budget is exhausted; only whatever is still diverging or implausible
    after that gets reset to its plain raw-sale value.
    """
    excluded_edges: frozenset[ConversionEdge] = frozenset()
    result = None
    for _ in range(MAX_DIVERGENCE_RETRIES):
        result = _solve_all_pass(
            edges_by_input, prices, npc_prices, tax_rate, max_iters, epsilon, excluded_edges, stocks
        )
        implausible = {
            iid for iid in result.universe if abs(result.value[iid]) > MAX_PLAUSIBLE_VALUE
        }
        culprits = _find_culprit_edges(
            result.still_changing, implausible, result.chosen_edge
        )
        if not result.still_changing and not implausible:
            break
        if not culprits:
            # Nothing we can point at a specific edge for -- e.g. a chain
            # too long to converge within max_iters, with no clean cycle
            # and no single item over the magnitude threshold yet. Blaming
            # every diverging item's edge here would blame innocent
            # bystanders whose only fault is depending on something still
            # resolving. Stop retrying and let the final magnitude/
            # still-changing check handle it.
            break
        new_excluded = excluded_edges | culprits
        if new_excluded == excluded_edges:
            break
        excluded_edges = new_excluded
    else:
        print(
            f"WARNING: profitability graph still diverging after "
            f"{MAX_DIVERGENCE_RETRIES} rounds of stripping culprit recipes"
        )

    if excluded_edges_out is not None:
        excluded_edges_out.update(excluded_edges)

    if excluded_edges:
        culprit_names = sorted({f"{e.process_type}: {e.name}" for e in excluded_edges})
        print(
            f"WARNING: excluded {len(excluded_edges)} recipe(s) that formed a "
            f"positive-gain value cycle with no fixed point (likely a recipe data "
            f"issue, e.g. a bad quantity ratio): {culprit_names}"
        )

    unreliable = result.still_changing | {
        iid for iid in result.universe if abs(result.value[iid]) > MAX_PLAUSIBLE_VALUE
    }
    if unreliable:
        sample = sorted(unreliable)[:10]
        more = f" (+{len(unreliable) - 10} more)" if len(unreliable) > 10 else ""
        print(
            f"WARNING: {len(unreliable)} item(s) still produced an unreliable/diverging "
            f"value even after excluding culprit recipes -- reset to plain raw-sale "
            f"value: {sample}{more}"
        )

    for iid in result.universe:
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
                action=result.action[iid],
                value_per_unit=result.value[iid],
                steps=result.steps[iid],
                process_types=result.process_types[iid],
                bonus_upside_per_unit=result.bonus_upside[iid],
                edge_chain=_reconstruct_edge_chain(iid, result.chosen_edge),
            )


def _reconstruct_edge_chain(
    item_id: int, chosen_edge: dict[int, ConversionEdge | None]
) -> tuple[ConversionEdge, ...]:
    """Walk ``chosen_edge`` from ``item_id`` to build the actual sequence of
    recipes used, matching the ``steps``/``action`` strings but as real
    ``ConversionEdge`` objects a caller can inspect (full ingredient list,
    quantities, mastery) rather than just names.
    """
    chain: list[ConversionEdge] = []
    node: int | None = item_id
    seen: set[int] = set()
    while node is not None and node not in seen:
        seen.add(node)
        edge = chosen_edge.get(node)
        if edge is None:
            break
        chain.append(edge)
        node = edge.base_outputs[0].item_id if edge.base_outputs else None
    return tuple(chain)


def _find_culprit_edges(
    still_changing: set[int],
    implausible: set[int],
    chosen_edge: dict[int, "ConversionEdge | None"],
) -> set[ConversionEdge]:
    """Which edges are actually responsible for a diverging or implausible
    value, restricted to still-diverging and implausible-magnitude items.

    Each suspect item points to at most one "next" item (its chosen edge's
    primary output), so this is a functional graph -- standard cycle
    detection by following chains and watching for a repeat within the
    current path finds genuine loops (e.g. Black Stone -> Powder -> ... ->
    Black Stone). Every edge on a detected loop is blamed, since a single
    exclusion per round converges far too slowly on a graph this tangled
    (confirmed on real data: hundreds of rounds still hadn't resolved a
    ~600-item residual). An item that merely depends on a bad value
    without being part of the loop itself is not on this walk's path at
    all once the state-bookkeeping bug below is accounted for, so its
    edge is correctly never blamed.

    Not every bad value comes from a literal cycle: a single recipe with a
    wrong ratio can inflate a whole acyclic chain above it without any
    node repeating. When a walk from a still-diverging/implausible item
    reaches a *plausible* item (or a leaf) without ever repeating, the
    deepest implausible node in that walk is where the value actually
    originates -- everything shallower than it just inherited an already-
    bad number through an otherwise-correct edge. Only that one edge is
    blamed in that case; a walk that bottoms out in the merely-still-
    changing set with no implausible node in it is left alone, since a
    long-but-sane chain that hasn't finished converging yet looks the
    same and blaming it would sink a legitimate item.

    The ``state`` visitation below is shared across every starting node's
    walk (standard practice, keeps this O(V)) -- but that means a walk can
    reach a node some *other* walk already fully resolved and marked
    "done". Confirmed on real data that treating that the same as
    "genuinely reached a plausible value" produced order-dependent false
    positives: ordinary ore smelting got blamed as an "origin" purely
    because Python's set iteration happened to explore an unrelated
    downstream item first. Only a walk that truly exits the suspect set
    (a plausible value or a leaf, not another walk's leftovers) is
    trusted for origin-blame.
    """
    suspect = still_changing | implausible
    next_item: dict[int, int] = {}
    for iid in suspect:
        edge = chosen_edge.get(iid)
        if edge is not None and edge.base_outputs:
            next_item[iid] = edge.base_outputs[0].item_id

    culprit_edges: set[ConversionEdge] = set()
    state: dict[int, int] = {}  # 0/absent=unvisited, 1=in current path, 2=done
    for start in suspect:
        if state.get(start, 0) != 0:
            continue
        path: list[int] = []
        node: int | None = start
        while node is not None and node in suspect and state.get(node, 0) == 0:
            state[node] = 1
            path.append(node)
            node = next_item.get(node)
        if node is not None and state.get(node) == 1:
            idx = path.index(node)
            for cycle_node in path[idx:]:
                edge = chosen_edge.get(cycle_node)
                if edge is not None:
                    culprit_edges.add(edge)
        elif (
            path
            and path[-1] in implausible
            and (node is None or node not in suspect)
        ):
            # The walk ended because it genuinely left the suspect set (a
            # plausible value or a leaf), not because it ran into a node
            # some other walk already finished with -- that would just be
            # bookkeeping order, not evidence this path's origin is here.
            edge = chosen_edge.get(path[-1])
            if edge is not None:
                culprit_edges.add(edge)
        for p in path:
            state[p] = 2
    return culprit_edges


@dataclass
class _SweepResult:
    value: dict[int, float]
    action: dict[int, str]
    steps: dict[int, tuple[str, ...]]
    process_types: dict[int, tuple[str, ...]]
    bonus_upside: dict[int, float]
    still_changing: set[int]
    chosen_edge: dict[int, ConversionEdge | None]
    universe: set[int]


def _solve_all_pass(
    edges_by_input: dict[int, list[ConversionEdge]],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    max_iters: int,
    epsilon: float,
    excluded_edges: frozenset[ConversionEdge],
    stocks: dict[int, float] | None = None,
) -> _SweepResult:
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
    chosen_edge: dict[int, ConversionEdge | None] = {iid: None for iid in universe}

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
            best_edge: ConversionEdge | None = None
            for edge in edges_by_input.get(iid, []):
                if edge in excluded_edges:
                    continue
                candidate = _evaluate_edge_relaxed(
                    edge, iid, value, action, steps, process_types, prices, npc_prices, stocks
                )
                if candidate is not None and candidate[0] > best_value:
                    best_value, best_action, best_steps, best_types, best_upside = candidate
                    best_edge = edge
            if abs(best_value - value[iid]) > epsilon:
                changed = True
                still_changing.add(iid)
            value[iid] = best_value
            action[iid] = best_action
            steps[iid] = best_steps
            process_types[iid] = best_types
            bonus_upside[iid] = best_upside
            chosen_edge[iid] = best_edge
        if not changed:
            break

    return _SweepResult(
        value=value,
        action=action,
        steps=steps,
        process_types=process_types,
        bonus_upside=bonus_upside,
        still_changing=still_changing,
        chosen_edge=chosen_edge,
        universe=universe,
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
    stocks: dict[int, float] | None = None,
) -> tuple[float, str, tuple[str, ...], tuple[str, ...], float] | None:
    target_qty = next((qty for iid, qty in edge.inputs if iid == target_item_id), None)
    if not target_qty:
        return None

    other_cost = sum(
        acquisition_cost(iid, prices, npc_prices, stocks) * qty
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
