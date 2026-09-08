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
