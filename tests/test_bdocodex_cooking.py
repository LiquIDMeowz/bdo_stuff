import json
from pathlib import Path

from bdo_profit.scraper.bdocodex import (
    parse_cooking_summary_row,
    parse_recipe_detail_ingredients,
    build_cooking_edge,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _cooking_rows():
    return json.loads((FIXTURES / "recipes_culinary_sample.json").read_text())["aaData"]


def test_parse_white_sauce_summary_no_bonus():
    row = _cooking_rows()[0]
    summary = parse_cooking_summary_row(row)
    assert summary["recipe_id"] == 106
    assert summary["name"] == "White Sauce"
    assert summary["primary_output"].item_id == 9003
    assert summary["bonus_outputs"] == []


def test_parse_pickled_vegetables_summary_has_bonus_dish():
    row = _cooking_rows()[1]
    summary = parse_cooking_summary_row(row)
    assert summary["name"] == "Pickled Vegetables"
    assert summary["primary_output"].item_id == 9202
    assert len(summary["bonus_outputs"]) == 1
    bonus = summary["bonus_outputs"][0]
    assert bonus.item_id == 9281
    assert bonus.chance is None


def test_parse_recipe_detail_ingredients():
    html = (FIXTURES / "recipe_106_detail_sample.html").read_text()
    ingredients = parse_recipe_detail_ingredients(html)
    assert ingredients == ((9018, 1.0), (9065, 1.0), (7313, 1.0), (9017, 2.0))


def test_build_cooking_edge_combines_summary_and_ingredients():
    row = _cooking_rows()[0]
    summary = parse_cooking_summary_row(row)
    ingredients = ((9018, 1.0), (9065, 1.0), (7313, 1.0), (9017, 2.0))
    edge = build_cooking_edge(summary, ingredients)
    assert edge.process_type == "Cooking"
    assert edge.inputs == ingredients
    assert edge.base_outputs[0].item_id == 9003


def test_scrape_cooking_recipes_skips_malformed_row_with_warning(capsys):
    from unittest.mock import MagicMock, patch
    from bdo_profit.scraper.bdocodex import scrape_cooking_recipes

    good_row = _cooking_rows()[0]
    bad_row = [999, "<no-name-tag/>", "<no-b-tag/>", "Cooking", {"sort_value": 0}, "0", "", "0", "", [], [], None, 0]

    with patch(
        "bdo_profit.scraper.bdocodex.fetch_recipes_json",
        return_value=[good_row, bad_row],
    ), patch(
        "bdo_profit.scraper.bdocodex.fetch_recipe_ingredients",
        return_value=((9018, 1.0),),
    ), patch("time.sleep", return_value=None):
        edges = scrape_cooking_recipes(MagicMock())

    assert len(edges) == 1
    assert edges[0].recipe_id == 106
    assert "WARNING" in capsys.readouterr().out
