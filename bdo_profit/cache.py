import json
import time
from dataclasses import asdict
from pathlib import Path

from bdo_profit.models import BonusOutput, ConversionEdge, YieldRange


def save_edges(edges: list[ConversionEdge], path: Path) -> None:
    payload = {
        "scraped_at": time.time(),
        "edges": [asdict(e) for e in edges],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def load_edges(path: Path) -> tuple[list[ConversionEdge], float]:
    payload = json.loads(path.read_text())
    edges = []
    for e in payload["edges"]:
        base_outputs = tuple(YieldRange(**yr) for yr in e["base_outputs"])
        bonus_outputs = tuple(BonusOutput(**bo) for bo in e["bonus_outputs"])
        edges.append(
            ConversionEdge(
                recipe_id=e["recipe_id"],
                name=e["name"],
                process_type=e["process_type"],
                mastery_required=e["mastery_required"],
                inputs=tuple(tuple(pair) for pair in e["inputs"]),
                base_outputs=base_outputs,
                bonus_outputs=bonus_outputs,
            )
        )
    return edges, payload["scraped_at"]


def load_exchanges(path: Path) -> list[ConversionEdge]:
    # Hand-maintained fixed-ratio NPC exchange-window conversions (e.g. Liana
    # in Velia trading Witch's Delicacy for Milk) -- not scraped from
    # bdocodex, so modeled as plain ConversionEdges with a deterministic
    # yield (qty_min == qty_max) and process_type "Exchange" so callers can
    # tell them apart from real crafting recipes.
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"WARNING: could not load exchanges from {path}: {exc}")
        return []
    edges = []
    for entry in raw:
        outputs = tuple(
            YieldRange(item_id=int(iid), qty_min=float(qty), qty_max=float(qty))
            for iid, qty in entry["outputs"]
        )
        edges.append(
            ConversionEdge(
                recipe_id=int(entry["recipe_id"]),
                name=entry["name"],
                process_type="Exchange",
                mastery_required=0,
                inputs=tuple((int(iid), float(qty)) for iid, qty in entry["inputs"]),
                base_outputs=outputs,
            )
        )
    return edges
