import json
from dataclasses import asdict
from pathlib import Path

from bdo_profit.market_client import MarketSnapshot


def save_price_cache(cache: dict[int, MarketSnapshot], path: Path) -> None:
    payload = {"prices": [asdict(snapshot) for snapshot in cache.values()]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def load_price_cache(path: Path) -> dict[int, MarketSnapshot]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: could not load price cache from {path}: {exc}")
        return {}
    try:
        return {
            entry["item_id"]: MarketSnapshot(**entry)
            for entry in payload["prices"]
        }
    except (KeyError, TypeError) as exc:
        print(f"WARNING: could not load price cache from {path}: {exc}")
        return {}
