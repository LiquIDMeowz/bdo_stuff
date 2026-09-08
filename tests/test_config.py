import json
from pathlib import Path

import pytest

from bdo_profit.config import load_mastery, load_npc_prices, load_bonus_proc_rates


def test_load_mastery(tmp_path: Path):
    p = tmp_path / "mastery.yaml"
    p.write_text(
        "gathering:\n  mastery: 375\n  level: 'Artisan 5'\n"
        "processing:\n  mastery: 385\n  level: 'Artisan 7'\n"
        "  subskills:\n    heating: 940\n"
        "cooking:\n  mastery: 1040\n  level: 'Master 7'\n"
    )
    cfg = load_mastery(p)
    assert cfg.gathering_mastery == 375
    assert cfg.processing_mastery == 385
    assert cfg.cooking_mastery == 1040
    assert cfg.processing_subskills == {"heating": 940}


def test_load_npc_prices(tmp_path: Path):
    p = tmp_path / "npc_prices.json"
    p.write_text(json.dumps({"5000": 14, "7315": 32}))
    prices = load_npc_prices(p)
    assert prices == {5000: 14.0, 7315: 32.0}


def test_load_bonus_proc_rates_empty_file(tmp_path: Path):
    p = tmp_path / "bonus_proc_rates.yaml"
    p.write_text("# empty\n")
    rates = load_bonus_proc_rates(p)
    assert rates == {}


def test_load_bonus_proc_rates_with_entries(tmp_path: Path):
    p = tmp_path / "bonus_proc_rates.yaml"
    p.write_text("112: 0.1\n")
    rates = load_bonus_proc_rates(p)
    assert rates == {112: 0.1}


def test_load_npc_prices_missing_file_warns_and_returns_empty(tmp_path: Path, capsys):
    missing = tmp_path / "does_not_exist.json"
    assert load_npc_prices(missing) == {}
    assert "WARNING" in capsys.readouterr().out


def test_load_bonus_proc_rates_missing_file_warns_and_returns_empty(tmp_path: Path, capsys):
    missing = tmp_path / "does_not_exist.yaml"
    assert load_bonus_proc_rates(missing) == {}
    assert "WARNING" in capsys.readouterr().out


def test_load_npc_prices_malformed_shape_returns_empty(tmp_path: Path):
    p = tmp_path / "npc_prices.json"
    p.write_text(json.dumps([1, 2, 3]))  # list, not a mapping
    assert load_npc_prices(p) == {}


def test_load_bonus_proc_rates_malformed_shape_returns_empty(tmp_path: Path):
    p = tmp_path / "bonus_proc_rates.yaml"
    p.write_text("- 112\n- 0.1\n")  # list, not a mapping
    assert load_bonus_proc_rates(p) == {}
