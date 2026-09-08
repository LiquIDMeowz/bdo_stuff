import json
from pathlib import Path

from bdo_profit.scraper.bdocodex import parse_mrecipe_row

FIXTURE = Path(__file__).parent / "fixtures" / "mrecipes_heating_sample.json"


def _rows():
    return json.loads(FIXTURE.read_text())["aaData"]


def test_parse_iron_ore_heating_row():
    row = _rows()[0]
    edge = parse_mrecipe_row(row)
    assert edge.recipe_id == 1
    assert edge.name == "Melted Iron Shard"
    assert edge.process_type == "Heating"
    assert edge.mastery_required == 0
    assert edge.inputs == ((4001, 5.0),)
    assert len(edge.base_outputs) == 1
    assert edge.base_outputs[0].item_id == 4051
    assert edge.base_outputs[0].qty_min == 1.0
    assert edge.base_outputs[0].qty_max == 4.0


def test_parse_row_captures_bonus_output():
    row = _rows()[0]
    edge = parse_mrecipe_row(row)
    assert len(edge.bonus_outputs) == 1
    bonus = edge.bonus_outputs[0]
    assert bonus.item_id == 4052
    assert bonus.chance is None


def test_parse_row_with_single_output_has_no_bonus():
    row = _rows()[1]  # Iron Ingot: single output item
    edge = parse_mrecipe_row(row)
    assert edge.name == "Iron Ingot"
    assert edge.bonus_outputs == ()


def test_scrape_processing_recipes_skips_malformed_row_with_warning(capsys):
    from unittest.mock import MagicMock, patch
    from bdo_profit.scraper.bdocodex import scrape_processing_recipes

    good_row = _rows()[0]
    bad_row = [999, "<div>no name tag here</div>", "<no-b-tag/>", "Heating", {"sort_value": 0}, "", "", "", "", [], [], 0, 0]

    with patch(
        "bdo_profit.scraper.bdocodex.fetch_mrecipes_json",
        return_value=[good_row, bad_row],
    ), patch(
        "bdo_profit.scraper.bdocodex.PROCESS_TYPES", {"heating": "Heating"}
    ), patch("time.sleep", return_value=None):
        edges = scrape_processing_recipes(MagicMock())

    assert len(edges) == 1
    assert edges[0].recipe_id == 1
    assert "WARNING" in capsys.readouterr().out
