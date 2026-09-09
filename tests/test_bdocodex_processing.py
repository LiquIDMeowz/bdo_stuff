import json
from pathlib import Path

from bdo_profit.scraper.bdocodex import PROCESS_TYPES, parse_mrecipe_row

FIXTURE = Path(__file__).parent / "fixtures" / "mrecipes_heating_sample.json"
MALCHEMY_FIXTURE = Path(__file__).parent / "fixtures" / "mrecipes_malchemy_sample.json"
BLACK_GEM_FIXTURE = (
    Path(__file__).parent / "fixtures" / "mrecipes_black_gem_duplicate_input_sample.json"
)


def _rows():
    return json.loads(FIXTURE.read_text())["aaData"]


def test_process_types_includes_simple_alchemy():
    assert PROCESS_TYPES["malchemy"] == "Simple Alchemy"


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


def test_parse_simple_alchemy_row_handles_multiple_inputs():
    # Simple Alchemy recipes (unlike Heating/Chopping/etc) commonly take more
    # than one input item -- confirms parse_mrecipe_row was never actually
    # restricted to single-input, it just hadn't been exercised with >1 before.
    row = json.loads(MALCHEMY_FIXTURE.read_text())["aaData"][0]
    edge = parse_mrecipe_row(row)
    assert edge.recipe_id == 561
    assert edge.name == "WON Magic Crystal - Harphia"
    assert edge.process_type == "Simple Alchemy"
    assert edge.inputs == ((15628, 1.0), (4918, 10.0), (4917, 1.0))
    assert edge.base_outputs[0].item_id == 15803
    assert {b.item_id for b in edge.bonus_outputs} == {15802, 15801}


def test_parse_row_merges_duplicate_input_item_ids():
    # Real bdocodex data: "Black Gem" (recipe 1768) lists item 16001 (Black
    # Stone) as two separate identical wrapper divs instead of one div with
    # quantity 2 -- confirmed live on bdocodex.com. Left unmerged, this halves
    # the apparent cost of the recipe (the engine only counts one of the two
    # entries when summing "other" ingredient costs, and only sees quantity 1
    # instead of 2 when valuing the duplicated item itself), which was found
    # to contribute to a real divergent/astronomical value on live data.
    row = json.loads(BLACK_GEM_FIXTURE.read_text())["aaData"][0]
    edge = parse_mrecipe_row(row)
    assert edge.name == "Black Gem"
    assert edge.inputs == ((4999, 1.0), (16001, 2.0))


def test_scrape_processing_recipes_skips_known_bad_recipe_with_reason(capsys):
    # Real bdocodex recipe 241, "Grinding: Black Stone", lists 2x Sharp Black
    # Crystal Shard -> 5x Black Stone -- verified live against bdocodex.com,
    # not a scraping error. Combined with recipe 2382 (which needs 1x Black
    # Stone as an ingredient to make Shards), this forms a genuine ~25x
    # positive-gain cycle: 2 Shards -> 5 Stones -> 25 Shards -> ... with no
    # fixed point, which produced a real 6.57e94-silver result on live data
    # before this exclusion. Almost certainly a data entry error on
    # bdocodex's side (the ratio is backwards from how BDO grinding/refining
    # normally works -- consuming more than it yields of a higher tier, not
    # less), not something fixable by correcting our own parsing.
    from unittest.mock import MagicMock, patch
    from bdo_profit.scraper.bdocodex import scrape_processing_recipes

    bad_row = [
        241,
        "<div class=\"iconset_wrapper_big\"><a href=\"/us/mrecipe/241/\"></a></div>",
        "<a href=\"/us/mrecipe/241/\"><b><span></span>Black Stone</b></a>",
        "Grinding",
        {"sort_value": 0},
        "",
        "<div class=\"iconset_wrapper_medium inlinediv\"><a data-id=\"item--4998\"><div class=\"quantity_small nowrap\">2</div></a></div>",
        "1.50",
        "<div class=\"iconset_wrapper_medium inlinediv\"><a data-id=\"item--16001\"><div class=\"quantity_small nowrap\">5</div></a></div>",
        "[4998]",
        "[]",
        7,
        0,
    ]

    with patch(
        "bdo_profit.scraper.bdocodex.fetch_mrecipes_json",
        return_value=[bad_row],
    ), patch(
        "bdo_profit.scraper.bdocodex.PROCESS_TYPES", {"grind": "Grinding"}
    ), patch("time.sleep", return_value=None):
        edges = scrape_processing_recipes(MagicMock())

    assert edges == []
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "241" in out


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
