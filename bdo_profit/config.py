import json
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class MasteryConfig:
    gathering_mastery: int
    processing_mastery: int
    cooking_mastery: int
    processing_subskills: dict[str, int]


def load_mastery(path: Path) -> MasteryConfig:
    data = yaml.safe_load(path.read_text())
    return MasteryConfig(
        gathering_mastery=data["gathering"]["mastery"],
        processing_mastery=data["processing"]["mastery"],
        cooking_mastery=data["cooking"]["mastery"],
        processing_subskills=data["processing"].get("subskills", {}),
    )


def load_npc_prices(path: Path) -> dict[int, float]:
    # These files are hand-edited; degrade gracefully rather than killing a run.
    try:
        raw = json.loads(path.read_text())
        return {int(k): float(v) for k, v in raw.items()}
    except (FileNotFoundError, json.JSONDecodeError, AttributeError, ValueError) as exc:
        print(f"WARNING: could not load NPC prices from {path}: {exc}")
        return {}


def load_bonus_proc_rates(path: Path) -> dict[int, float]:
    try:
        data = yaml.safe_load(path.read_text())
        if not data:
            return {}
        return {int(k): float(v) for k, v in data.items()}
    except (FileNotFoundError, yaml.YAMLError, AttributeError, ValueError) as exc:
        print(f"WARNING: could not load bonus proc rates from {path}: {exc}")
        return {}


@dataclass
class UniversalProc:
    item_id: int
    item_name: str
    chance: float
    qty: float


def load_universal_procs(path: Path) -> dict[str, UniversalProc]:
    # Confirmed by the user: some proc chances apply to ANY recipe of a given
    # process_type (e.g. every Cooking action has ~2% odds of also yielding
    # Witch's Delicacy) -- this isn't per-recipe data bdocodex exposes, so it's
    # hand-maintained here, same as npc_prices.json.
    try:
        raw = json.loads(path.read_text())
        return {
            process_type: UniversalProc(
                item_id=int(entry["item_id"]),
                item_name=str(entry["item_name"]),
                chance=float(entry["chance"]),
                qty=float(entry["qty"]),
            )
            for process_type, entry in raw.items()
        }
    except (FileNotFoundError, json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError) as exc:
        print(f"WARNING: could not load universal procs from {path}: {exc}")
        return {}
