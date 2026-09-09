import json
from pathlib import Path

from bdo_profit.cache import save_edges, load_edges, load_exchanges
from bdo_profit.models import BonusOutput, ConversionEdge, YieldRange


def _sample_edges():
    return [
        ConversionEdge(
            recipe_id=1,
            name="Melted Iron Shard",
            process_type="Heating",
            mastery_required=0,
            inputs=((4001, 5.0),),
            base_outputs=(YieldRange(4051, 1, 4),),
            bonus_outputs=(BonusOutput(4052, 1, 1, chance=None),),
        )
    ]


def test_save_and_load_round_trip(tmp_path: Path):
    path = tmp_path / "cache.json"
    save_edges(_sample_edges(), path)
    loaded, scraped_at = load_edges(path)
    assert scraped_at > 0
    assert len(loaded) == 1
    edge = loaded[0]
    assert edge.recipe_id == 1
    assert edge.inputs == ((4001, 5.0),)
    assert edge.base_outputs == (YieldRange(4051, 1.0, 4.0),)
    assert edge.bonus_outputs == (BonusOutput(4052, 1.0, 1.0, chance=None),)


def test_load_exchanges(tmp_path: Path):
    path = tmp_path / "exchanges.json"
    path.write_text(json.dumps([
        {
            "recipe_id": -1001,
            "name": "Liana Exchange: Witch's Delicacy -> Milk",
            "inputs": [[9780, 10]],
            "outputs": [[9065, 120]],
        }
    ]))
    edges = load_exchanges(path)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.recipe_id == -1001
    assert edge.process_type == "Exchange"
    assert edge.mastery_required == 0
    assert edge.inputs == ((9780, 10.0),)
    assert edge.base_outputs == (YieldRange(9065, 120.0, 120.0),)


def test_load_exchanges_missing_file_warns_and_returns_empty(tmp_path: Path, capsys):
    missing = tmp_path / "does_not_exist.json"
    assert load_exchanges(missing) == []
    assert "WARNING" in capsys.readouterr().out
