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
class ExplainResult:
    item_id: int
    qty: float
    raw_sell_total: float
    processed_total: float
    worst_case_total: float
    steps: tuple[ExplainStep, ...]
    bonus_upside_total: float
    path_result: PathResult


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


def _compute_worst_case_total(
    steps: tuple[ExplainStep, ...], qty: float, prices: dict[int, float], tax_rate: float
) -> float:
    """Total silver if every hop yields its minimum instead of its average --
    confirmed by the user (Metal Solvent: profitable on average, a real loss
    at minimum yield) that average-case math alone hides real risk on
    thin-margin recipes.

    A lower yield at one hop doesn't just cut revenue there -- it means
    fewer units flow into every hop after it too, so this re-walks the
    whole chain with its own parallel running quantity rather than just
    scaling the existing average-case numbers down. Side-ingredient unit
    costs and sourcing choices are reused as-is from the average-case walk
    (needing less, not more, so whatever was achievable there remains
    achievable here); only the quantities are recomputed.
    """
    current_qty_worst = qty
    total_side_cost = 0.0
    final_item_id = None
    for step in steps:
        target_qty = step.primary_input_qty / step.batches
        batches_worst = current_qty_worst / target_qty
        primary_out = step.edge.base_outputs[0]
        for plan, avg_needed in step.side_ingredients:
            per_batch = avg_needed / step.batches
            total_side_cost += plan.unit_cost * (batches_worst * per_batch)
        final_item_id = primary_out.item_id
        current_qty_worst = batches_worst * primary_out.qty_min

    final_sell_value = prices.get(final_item_id, 0.0) * tax_rate
    return current_qty_worst * final_sell_value - total_side_cost


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
        return ExplainResult(item_id, qty, raw_sell_total, raw_sell_total, raw_sell_total, (), 0.0, result)

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
    worst_case_total = _compute_worst_case_total(tuple(steps), qty, prices, tax_rate)
    bonus_upside_total = result.bonus_upside_per_unit * qty
    return ExplainResult(
        item_id, qty, raw_sell_total, processed_total, worst_case_total,
        tuple(steps), bonus_upside_total, result,
    )
