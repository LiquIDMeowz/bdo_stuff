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
    steps: tuple[ExplainStep, ...]
    bonus_upside_total: float
    path_result: PathResult


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
        return ExplainResult(item_id, qty, raw_sell_total, raw_sell_total, (), 0.0, result)

    frozen_excluded = frozenset(excluded_edges)
    steps: list[ExplainStep] = []
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
                iid, edges_by_output, prices, npc_prices, acq_memo, frozen_excluded,
                stocks=stocks,
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

    processed_total = result.value_per_unit * qty
    bonus_upside_total = result.bonus_upside_per_unit * qty
    return ExplainResult(
        item_id, qty, raw_sell_total, processed_total, tuple(steps), bonus_upside_total, result
    )
