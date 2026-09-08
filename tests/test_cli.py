from pathlib import Path
from unittest.mock import patch

from bdo_profit.cli import main
from bdo_profit.models import ConversionEdge, YieldRange


FAKE_EDGES = [
    ConversionEdge(
        recipe_id=1,
        name="Melted Iron Shard",
        process_type="Heating",
        mastery_required=0,
        inputs=((4001, 5.0),),
        base_outputs=(YieldRange(4051, 1.0, 4.0),),
    )
]


def test_main_prints_ranked_table_and_writes_csv(tmp_path: Path, capsys):
    csv_path = tmp_path / "out.csv"
    npc_path = tmp_path / "npc.json"
    npc_path.write_text("{}")
    bonus_path = tmp_path / "bonus.yaml"
    bonus_path.write_text("")
    cache_path = tmp_path / "cache.json"

    fake_prices = {4001: {"name": "Iron Ore", "price": 100.0}, 4051: {"name": "Melted Iron Shard", "price": 2000.0}}

    class FakeSnapshot:
        def __init__(self, name, price):
            self.name = name
            self.price = price

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def get_price(self, item_id, sub_id=0):
            data = fake_prices.get(item_id)
            return FakeSnapshot(data["name"], data["price"]) if data else None

    with patch("bdo_profit.cli.MarketClient", FakeClient), \
         patch("bdo_profit.cli.scrape_all", return_value=FAKE_EDGES):
        main([
            "--refresh-recipes",
            "--cache-path", str(cache_path),
            "--npc-prices-path", str(npc_path),
            "--bonus-rates-path", str(bonus_path),
            "--csv", str(csv_path),
            "--top", "5",
        ])

    captured = capsys.readouterr()
    assert "Iron Ore" in captured.out
    assert csv_path.exists()
    assert "Iron Ore" in csv_path.read_text()
