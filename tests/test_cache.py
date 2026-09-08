from pathlib import Path

from bdo_profit.cache import save_edges, load_edges
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
