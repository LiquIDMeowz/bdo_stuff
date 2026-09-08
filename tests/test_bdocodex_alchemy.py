import json
from pathlib import Path

from bdo_profit.scraper.bdocodex import (
    parse_alchemy_summary_row,
    parse_recipe_detail_ingredients,
    build_alchemy_edge,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _alchemy_rows():
    return json.loads((FIXTURES / "recipes_alchemy_sample.json").read_text())["aaData"]


def test_parse_elixir_of_amity_summary():
    row = _alchemy_rows()[0]
    summary = parse_alchemy_summary_row(row)
    assert summary["recipe_id"] == 1
    assert summary["name"] == "Elixir of Amity"
    assert summary["mastery_required"] == 11
    assert summary["primary_output"].item_id == 664
    assert len(summary["bonus_outputs"]) == 2
    assert {b.item_id for b in summary["bonus_outputs"]} == {665, 9733}
    assert all(b.chance is None for b in summary["bonus_outputs"])


def test_parse_alchemy_recipe_detail_ingredients():
    html = (FIXTURES / "recipe_1_alchemy_detail_sample.html").read_text()
    ingredients = parse_recipe_detail_ingredients(html)
    assert ingredients == ((6351, 1.0), (5403, 5.0), (5001, 6.0), (4901, 3.0))


def test_build_alchemy_edge_combines_summary_and_ingredients():
    row = _alchemy_rows()[0]
    summary = parse_alchemy_summary_row(row)
    ingredients = ((6351, 1.0), (5403, 5.0), (5001, 6.0), (4901, 3.0))
    edge = build_alchemy_edge(summary, ingredients)
    assert edge.process_type == "Alchemy"
    assert edge.inputs == ingredients
    assert edge.base_outputs[0].item_id == 664


def test_scrape_alchemy_recipes_skips_malformed_row_with_warning(capsys):
    from unittest.mock import MagicMock, patch
    from bdo_profit.scraper.bdocodex import scrape_alchemy_recipes

    good_row = _alchemy_rows()[0]
    bad_row = [999, "<no-name-tag/>", "<no-b-tag/>", "Alchemy", {"sort_value": 0}, "0", "", "0", "", [], [], None, 0]

    with patch(
        "bdo_profit.scraper.bdocodex.fetch_recipes_json",
        return_value=[good_row, bad_row],
    ), patch(
        "bdo_profit.scraper.bdocodex.fetch_recipe_ingredients",
        return_value=((6351, 1.0),),
    ), patch("time.sleep", return_value=None):
        edges = scrape_alchemy_recipes(MagicMock())

    assert len(edges) == 1
    assert edges[0].recipe_id == 1
    assert "WARNING" in capsys.readouterr().out
