from dataclasses import dataclass

from bdo_profit.models import ConversionEdge
from bdo_profit.profitability import (
    AcquisitionPlan,
    PathResult,
    best_path_value,
    build_edges_by_input,
    build_edges_by_output,
    cheapest_acquisition_plan,
)


@dataclass
class ExplainStep:
    edge: ConversionEdge
    primary_input_qty: float  # how much of the incoming spine item this hop consumes
    batches: float
    output_qty_expected: float
    side_ingredients: tuple[tuple[AcquisitionPlan, float], ...]  # (plan, total qty needed)


@dataclass
class StopPoint:
    """The outcome of stopping the chain after a given step and selling
    whatever you're holding at that point, instead of continuing to
    process further."""

    step_index: int  # -1 = sell the starting item raw, no processing at all
    item_id: int  # the item you'd be selling if you stopped here
    qty_avg: float
    qty_worst: float
    value_avg: float
    value_worst: float


@dataclass
class ExplainResult:
    item_id: int
    qty: float
    raw_sell_total: float
    processed_total: float
    worst_case_total: float
    steps: tuple[ExplainStep, ...]
    bonus_upside_total: float
    path_result: PathResult
    stop_points: tuple[StopPoint, ...] = ()
    recommended_stop_index: int = -1
    recommended_stop_value: float = 0.0


# Worst-case (minimum) yield is only tracked for these process types --
# confirmed by the user with a real example (Copper Ore) that treating every
# batch as independently able to hit bdocodex's literal minimum yield, with
# no benefit from doing it thousands of times, is statistically absurd for
# basic Processing (mastery_required=0, and the user's Processing mastery is
# near-max) and flagged routine ore smelting as "risky", which it isn't in
# practice. Alchemy/Cooking are kept because the user's own observations
# (a recipe yielding "1-2 in practice" vs bdocodex's stated 1-4) confirm
# real yields there run well below the stated maximum at their mastery.
WORST_CASE_TRACKED_TYPES = frozenset({"Alchemy", "Simple Alchemy", "Cooking"})

# Needing more than this many times the current sell-side listings counts as
# "far short" -- confirmed by the user (Black Warrior Horn Bow: stock 3,
# needed 833+) that a nonzero-but-nowhere-near-enough stock is just as
# unachievable as literal zero, not merely worth a warning.
STOCK_INSUFFICIENCY_THRESHOLD = 10.0
# Some items (e.g. Trace of Nature) have hundreds of alternative recipes, each
# keyed off a different specific rare catalyst -- confirmed on real data (262
# recipes for one item) that 8 rounds isn't enough to cycle through enough of
# them to land on something actually achievable.
MAX_STOCK_RETRIES = 300


def _find_understocked_items(
    plan: AcquisitionPlan, qty_needed: float, found: set[int]
) -> None:
    """Walk a built acquisition plan looking for any buy-market node where
    the real quantity needed at that point in the tree is far beyond
    current stock. ``cheapest_acquisition_plan`` only ever prices one unit,
    so it can't make this call itself -- the caller has to propagate the
    real quantity down through each craft hop (dividing by yield to get
    batches, same math the display layer already does) to know what's
    actually needed at each leaf.
    """
    if plan.method == "buy_market" and plan.available_stock is not None:
        if qty_needed > STOCK_INSUFFICIENCY_THRESHOLD * plan.available_stock:
            found.add(plan.item_id)
        return
    if plan.method == "craft" and plan.yield_expected:
        batches_needed = qty_needed / plan.yield_expected
        for sub_plan, sub_qty_per_batch in plan.inputs:
            _find_understocked_items(sub_plan, batches_needed * sub_qty_per_batch, found)


def _compute_stop_points(
    steps: tuple[ExplainStep, ...], start_item_id: int, qty: float,
    prices: dict[int, float], tax_rate: float,
) -> list[StopPoint]:
    """The outcome of stopping after each step (including not processing at
    all) instead of assuming you always push all the way to the end.

    Confirmed valuable by the user with a real example (Copper Ore: steps 1
    and 2 are plain ore smelting with no side ingredients, step 3 pulls in
    the expensive, risky Metal Solvent) -- a chain can be worth pushing
    partway through and then selling the intermediate product, even when
    the *full* chain isn't worth the risk. Every intermediate product in a
    processing chain is itself a real, sellable item, so "stop here" is
    always a valid option, not just "all the way" or "not at all".

    Both average and worst-case running quantities are tracked in the same
    pass (mirrors the two walks previously done separately for
    ``processed_total``/``worst_case_total``) so every stop point gets
    both figures.
    """
    points: list[StopPoint] = []
    raw_value = prices.get(start_item_id, 0.0) * tax_rate * qty
    points.append(StopPoint(-1, start_item_id, qty, qty, raw_value, raw_value))

    current_qty_avg = qty
    current_qty_worst = qty
    cum_side_cost_avg = 0.0
    cum_side_cost_worst = 0.0
    current_item_id = start_item_id

    for i, step in enumerate(steps):
        target_qty = step.primary_input_qty / step.batches
        batches_avg = current_qty_avg / target_qty
        batches_worst = current_qty_worst / target_qty
        primary_out = step.edge.base_outputs[0]

        for plan, avg_needed in step.side_ingredients:
            per_batch = avg_needed / step.batches
            cum_side_cost_avg += plan.unit_cost * (batches_avg * per_batch)
            cum_side_cost_worst += plan.unit_cost * (batches_worst * per_batch)

        current_qty_avg = batches_avg * primary_out.expected_qty
        step_yield = (
            primary_out.qty_min
            if step.edge.process_type in WORST_CASE_TRACKED_TYPES
            else primary_out.expected_qty
        )
        current_qty_worst = batches_worst * step_yield
        current_item_id = primary_out.item_id

        sell_value = prices.get(current_item_id, 0.0) * tax_rate
        value_avg = current_qty_avg * sell_value - cum_side_cost_avg
        value_worst = current_qty_worst * sell_value - cum_side_cost_worst
        points.append(
            StopPoint(i, current_item_id, current_qty_avg, current_qty_worst, value_avg, value_worst)
        )

    return points


def _recommend_stop_point(points: list[StopPoint]) -> StopPoint:
    """The best stopping point to actually commit to, walking the chain one
    step at a time and only taking a step if its worst case doesn't risk
    landing below what's already guaranteed by stopping now.

    Comparing every point's worst case against the raw baseline alone
    isn't enough: a step whose worst case still beats raw can nonetheless
    be worse than an already-guaranteed earlier stop (confirmed by the
    user with a real example -- Copper Ore's ore-smelting steps are safe
    and profitable on their own, but the next step pulls in expensive,
    thin-margin Metal Solvent; its worst case might still clear the raw
    price yet fall well short of what smelting alone already locked in).
    Since reaching any later step means physically passing through every
    step before it, the first step that would risk giving back the
    already-guaranteed value stops the walk there -- a later step looking
    fine "on paper" doesn't matter if you can't reach it without first
    accepting that risk.
    """
    best_stop = points[0]
    for point in points[1:]:
        if point.value_worst >= best_stop.value_avg:
            best_stop = point
        else:
            break
    return best_stop


def build_explain(
    item_id: int,
    qty: float,
    edges: list[ConversionEdge],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    stocks: dict[int, float] | None = None,
    memo: dict[int, PathResult] | None = None,
    acq_memo: dict[int, AcquisitionPlan] | None = None,
    edges_by_input: dict[int, list[ConversionEdge]] | None = None,
    edges_by_output: dict[int, list[ConversionEdge]] | None = None,
    excluded_edges: set[ConversionEdge] | None = None,
) -> ExplainResult:
    """Full step-by-step plan for turning ``qty`` units of ``item_id`` into
    silver: every hop of the chosen forward chain, with quantities scaled to
    ``qty``, plus a recursive buy-vs-craft recommendation for every side
    ingredient needed along the way (not just the primary material).

    ``memo``/``acq_memo``/``edges_by_input``/``edges_by_output``/
    ``excluded_edges`` can be passed in and reused across multiple
    ``build_explain`` calls -- solving the whole graph (``best_path_value``'s
    ``_solve_all``) is expensive, and without sharing this, explaining N
    items redundantly re-solves the entire graph N times. A caller
    explaining a single item can omit them and a fresh, self-contained set
    is built.

    ``excluded_edges`` specifically must be passed alongside ``memo`` (not
    just ``memo`` alone): ``best_path_value`` only populates its
    ``excluded_edges_out`` when it actually runs ``_solve_all``, which it
    skips once an item is already memoized -- without a shared, persisted
    ``excluded_edges``, every call after the first would silently see an
    empty set and let ``cheapest_acquisition_plan`` recommend crafting via
    a recipe the forward solver had already identified as bad.
    """
    edges_by_input = edges_by_input if edges_by_input is not None else build_edges_by_input(edges)
    edges_by_output = edges_by_output if edges_by_output is not None else build_edges_by_output(edges)
    memo = memo if memo is not None else {}
    acq_memo = acq_memo if acq_memo is not None else {}
    # Passing the same set object across repeated calls is what matters here:
    # _solve_all only runs (and populates it) once, on whichever call first
    # solves the graph -- every later call for a different item finds its
    # result already memoized and skips _solve_all entirely, but the set
    # still holds what got populated that first time.
    excluded_edges = excluded_edges if excluded_edges is not None else set()
    stocks = stocks or {}
    result = best_path_value(
        item_id, edges_by_input, prices, npc_prices, tax_rate, memo,
        excluded_edges_out=excluded_edges, stocks=stocks,
    )

    raw_sell_total = prices.get(item_id, 0.0) * tax_rate * qty

    if result.action == "sell_raw" or not result.edge_chain:
        only_point = StopPoint(-1, item_id, qty, qty, raw_sell_total, raw_sell_total)
        return ExplainResult(
            item_id, qty, raw_sell_total, raw_sell_total, raw_sell_total, (), 0.0, result,
            stop_points=(only_point,), recommended_stop_index=-1, recommended_stop_value=raw_sell_total,
        )

    frozen_excluded = frozenset(excluded_edges)
    blocked_market_items: frozenset[int] = frozenset()
    steps: list[ExplainStep] = []
    for _ in range(MAX_STOCK_RETRIES):
        acq_memo_round: dict[int, AcquisitionPlan] = {}
        steps = []
        current_item = item_id
        current_qty = qty
        for edge in result.edge_chain:
            target_qty = next(q for iid, q in edge.inputs if iid == current_item)
            batches = current_qty / target_qty
            primary_out = edge.base_outputs[0]
            output_qty = batches * primary_out.expected_qty

            side_ingredients: list[tuple[AcquisitionPlan, float]] = []
            for iid, req_qty in edge.inputs:
                if iid == current_item:
                    continue
                total_needed = batches * req_qty
                plan = cheapest_acquisition_plan(
                    iid, edges_by_output, prices, npc_prices, acq_memo_round, frozen_excluded,
                    stocks=stocks, blocked_market_items=blocked_market_items,
                )
                side_ingredients.append((plan, total_needed))

            steps.append(
                ExplainStep(
                    edge=edge,
                    primary_input_qty=current_qty,
                    batches=batches,
                    output_qty_expected=output_qty,
                    side_ingredients=tuple(side_ingredients),
                )
            )
            current_item = primary_out.item_id
            current_qty = output_qty

        understocked: set[int] = set()
        for step in steps:
            for plan, total_needed in step.side_ingredients:
                _find_understocked_items(plan, total_needed, understocked)
        new_blocked = blocked_market_items | understocked
        if new_blocked == blocked_market_items:
            break
        blocked_market_items = new_blocked
    # acq_memo (the caller-shared one) picks up whatever the final, stable
    # round settled on, so later build_explain calls sharing it inherit a
    # correct starting point instead of the first round's stale plans.
    acq_memo.update(acq_memo_round)

    processed_total = result.value_per_unit * qty
    stop_points = _compute_stop_points(tuple(steps), item_id, qty, prices, tax_rate)
    worst_case_total = stop_points[-1].value_worst
    recommended = _recommend_stop_point(stop_points)
    bonus_upside_total = result.bonus_upside_per_unit * qty
    return ExplainResult(
        item_id, qty, raw_sell_total, processed_total, worst_case_total,
        tuple(steps), bonus_upside_total, result,
        stop_points=tuple(stop_points),
        recommended_stop_index=recommended.step_index,
        recommended_stop_value=recommended.value_avg,
    )
