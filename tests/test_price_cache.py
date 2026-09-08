from pathlib import Path

from bdo_profit.market_client import MarketSnapshot
from bdo_profit.price_cache import load_price_cache, save_price_cache


def _sample_snapshot():
    return MarketSnapshot(
        item_id=4001,
        sub_id=0,
        name="Iron Ore",
        price=100.0,
        current_stock=500,
        total_trades=123456,
        last_sold_price=95.0,
        fetched_at=1700000000.0,
    )


def test_save_and_load_round_trip(tmp_path: Path):
    path = tmp_path / "price_cache.json"
    save_price_cache({4001: _sample_snapshot()}, path)
    loaded = load_price_cache(path)
    assert loaded == {4001: _sample_snapshot()}


def test_load_missing_file_returns_empty_dict(tmp_path: Path):
    loaded = load_price_cache(tmp_path / "does_not_exist.json")
    assert loaded == {}


def test_load_malformed_file_warns_and_returns_empty(tmp_path: Path, capsys):
    path = tmp_path / "price_cache.json"
    path.write_text("not valid json")
    loaded = load_price_cache(path)
    assert loaded == {}
    assert "WARNING" in capsys.readouterr().out
