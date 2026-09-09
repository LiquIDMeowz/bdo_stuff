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
) -> ExplainResult:
    """Full step-by-step plan for turning ``qty`` units of ``item_id`` into
    silver: every hop of the chosen forward chain, with quantities scaled to
    ``qty``, plus a recursive buy-vs-craft recommendation for every side
    ingredient needed along the way (not just the primary material).
    """
    edges_by_input = build_edges_by_input(edges)
    edges_by_output = build_edges_by_output(edges)
    excluded: set[ConversionEdge] = set()
    memo: dict[int, PathResult] = {}
    result = best_path_value(
        item_id, edges_by_input, prices, npc_prices, tax_rate, memo, excluded_edges_out=excluded
    )

    raw_sell_total = prices.get(item_id, 0.0) * tax_rate * qty

    if result.action == "sell_raw" or not result.edge_chain:
        return ExplainResult(item_id, qty, raw_sell_total, raw_sell_total, (), 0.0, result)

    frozen_excluded = frozenset(excluded)
    acq_memo: dict[int, AcquisitionPlan] = {}
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
                iid, edges_by_output, prices, npc_prices, acq_memo, frozen_excluded
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
