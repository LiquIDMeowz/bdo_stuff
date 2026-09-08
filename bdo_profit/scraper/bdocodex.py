import json
import time

import requests
from bs4 import BeautifulSoup

from bdo_profit.models import BonusOutput, ConversionEdge, YieldRange

BASE_URL = "https://bdocodex.com"

PROCESS_TYPES = {
    "heating": "Heating",
    "woodcutting": "Chopping",
    "grind": "Grinding",
    "dry": "Drying",
    "thinning": "Filtering",
    "shake": "Shaking",
    "malchemy": "Simple Alchemy",
}

def fetch_mrecipes_json(process_slug: str, session: requests.Session) -> list[list]:
    resp = session.get(
        f"{BASE_URL}/query.php",
        params={"a": "mrecipes", "type": process_slug, "id": 1, "l": "us"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = json.loads(resp.text.lstrip("﻿"))
    return payload["aaData"]


def _extract_item_entries(cell_html: str) -> list[tuple[int, float, float]]:
    """Parse a table cell's HTML into (item_id, qty_min, qty_max) tuples."""
    soup = BeautifulSoup(cell_html, "html.parser")
    entries = []
    for wrapper in soup.select("div.iconset_wrapper_medium"):
        link = wrapper.select_one("a[data-id^='item--']")
        if link is None:
            continue
        item_id = int(link["data-id"].split("--")[1])
        qty_div = wrapper.select_one("div.quantity_small")
        qty_text = qty_div.get_text(strip=True) if qty_div else "1"
        if "~" in qty_text:
            lo, hi = qty_text.split("~")
            qty_min, qty_max = float(lo), float(hi)
        else:
            qty_min = qty_max = float(qty_text)
        entries.append((item_id, qty_min, qty_max))
    return entries


def _primary_and_bonus_outputs(
    output_cell_html: str,
) -> tuple[YieldRange | None, list[BonusOutput]]:
    """The first item listed in the output cell is the guaranteed primary
    product; any further items are bonus/alternate outputs with an unknown
    proc rate.

    Earlier versions matched the recipe's icon-cell image filename against
    the output items instead, on the assumption the icon's embedded number
    is the primary item's id. That assumption is wrong for a meaningful
    fraction of real recipes (icon asset numbering and item id diverge --
    confirmed on real bdocodex data: ~18% of multi-output Simple Alchemy
    recipes, most of which matched nothing at all in the output cell and
    silently lost their real primary output into bonus_outputs). Output
    cell ordering has held as primary-first in every real recipe checked
    across Processing, Cooking, and Alchemy, and needs no extra network
    call to verify.
    """
    entries = _extract_item_entries(output_cell_html)
    if not entries:
        return None, []
    primary_id, primary_qmin, primary_qmax = entries[0]
    primary = YieldRange(primary_id, primary_qmin, primary_qmax)
    bonus = [
        BonusOutput(item_id, qty_min, qty_max, chance=None)
        for item_id, qty_min, qty_max in entries[1:]
    ]
    return primary, bonus


def parse_mrecipe_row(row: list) -> ConversionEdge:
    recipe_id = row[0]
    name = BeautifulSoup(row[2], "html.parser").select_one("b").get_text(strip=True)
    process_type = row[3]
    mastery_required = row[4]["sort_value"]
    inputs = tuple((iid, qmin) for iid, qmin, _qmax in _extract_item_entries(row[6]))
    primary, bonus = _primary_and_bonus_outputs(row[8])
    return ConversionEdge(
        recipe_id=recipe_id,
        name=name,
        process_type=process_type,
        mastery_required=mastery_required,
        inputs=inputs,
        base_outputs=(primary,) if primary else (),
        bonus_outputs=tuple(bonus),
    )


def scrape_processing_recipes(session: requests.Session) -> list[ConversionEdge]:
    edges: list[ConversionEdge] = []
    for slug in PROCESS_TYPES:
        rows = fetch_mrecipes_json(slug, session)
        for row in rows:
            try:
                edges.append(parse_mrecipe_row(row))
            except (AttributeError, IndexError, KeyError, ValueError) as exc:
                recipe_id = row[0] if row else "?"
                print(f"  WARNING: skipped {slug} recipe {recipe_id}: {exc}")
        time.sleep(0.5)
    return edges


def fetch_recipes_json(recipe_type: str, session: requests.Session) -> list[list]:
    resp = session.get(
        f"{BASE_URL}/query.php",
        params={"a": "recipes", "type": recipe_type, "id": 1, "l": "us"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = json.loads(resp.text.lstrip("﻿"))
    return payload["aaData"]


def parse_cooking_summary_row(row: list) -> dict:
    name = BeautifulSoup(row[2], "html.parser").select_one("b").get_text(strip=True)
    mastery_required = row[4]["sort_value"]
    primary, bonus = _primary_and_bonus_outputs(row[8])
    return {
        "recipe_id": row[0],
        "name": name,
        "mastery_required": mastery_required,
        "primary_output": primary,
        "bonus_outputs": bonus,
    }


def parse_recipe_detail_ingredients(html: str) -> tuple[tuple[int, float], ...]:
    soup = BeautifulSoup(html, "html.parser")
    label = soup.find(
        "span", class_="yellow_text", string=lambda s: s and "Crafting Materials" in s
    )
    if label is None:
        return ()
    container = label.find_parent("td")
    if container is None:
        return ()
    ingredients = []
    for item_id, qty_min, _qty_max in _extract_item_entries(str(container)):
        ingredients.append((item_id, qty_min))
    return tuple(ingredients)


def fetch_recipe_ingredients(
    recipe_id: int, session: requests.Session
) -> tuple[tuple[int, float], ...]:
    resp = session.get(
        f"{BASE_URL}/us/recipe/{recipe_id}/",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    return parse_recipe_detail_ingredients(resp.text)


def build_cooking_edge(
    summary: dict, ingredients: tuple[tuple[int, float], ...]
) -> ConversionEdge:
    primary = summary["primary_output"]
    return ConversionEdge(
        recipe_id=summary["recipe_id"],
        name=summary["name"],
        process_type="Cooking",
        mastery_required=summary["mastery_required"],
        inputs=ingredients,
        base_outputs=(primary,) if primary else (),
        bonus_outputs=tuple(summary["bonus_outputs"]),
    )


def scrape_cooking_recipes(session: requests.Session) -> list[ConversionEdge]:
    rows = fetch_recipes_json("culinary", session)
    edges = []
    for i, row in enumerate(rows):
        try:
            summary = parse_cooking_summary_row(row)
            ingredients = fetch_recipe_ingredients(summary["recipe_id"], session)
            edges.append(build_cooking_edge(summary, ingredients))
        except (AttributeError, IndexError, KeyError, ValueError, requests.RequestException) as exc:
            recipe_id = row[0] if row else "?"
            print(f"  WARNING: skipped cooking recipe {recipe_id}: {exc}")
        time.sleep(0.5)
        if (i + 1) % 25 == 0:
            print(f"  scraped {i + 1}/{len(rows)} cooking recipes...")
    return edges


def parse_alchemy_summary_row(row: list) -> dict:
    name = BeautifulSoup(row[2], "html.parser").select_one("b").get_text(strip=True)
    mastery_required = row[4]["sort_value"]
    primary, bonus = _primary_and_bonus_outputs(row[8])
    return {
        "recipe_id": row[0],
        "name": name,
        "mastery_required": mastery_required,
        "primary_output": primary,
        "bonus_outputs": bonus,
    }


def build_alchemy_edge(
    summary: dict, ingredients: tuple[tuple[int, float], ...]
) -> ConversionEdge:
    primary = summary["primary_output"]
    return ConversionEdge(
        recipe_id=summary["recipe_id"],
        name=summary["name"],
        process_type="Alchemy",
        mastery_required=summary["mastery_required"],
        inputs=ingredients,
        base_outputs=(primary,) if primary else (),
        bonus_outputs=tuple(summary["bonus_outputs"]),
    )


def scrape_alchemy_recipes(session: requests.Session) -> list[ConversionEdge]:
    rows = fetch_recipes_json("alchemy", session)
    edges = []
    for i, row in enumerate(rows):
        try:
            summary = parse_alchemy_summary_row(row)
            ingredients = fetch_recipe_ingredients(summary["recipe_id"], session)
            edges.append(build_alchemy_edge(summary, ingredients))
        except (AttributeError, IndexError, KeyError, ValueError, requests.RequestException) as exc:
            recipe_id = row[0] if row else "?"
            print(f"  WARNING: skipped alchemy recipe {recipe_id}: {exc}")
        time.sleep(0.5)
        if (i + 1) % 25 == 0:
            print(f"  scraped {i + 1}/{len(rows)} alchemy recipes...")
    return edges


def scrape_all(session: requests.Session | None = None) -> list[ConversionEdge]:
    session = session or requests.Session()
    print("Scraping processing recipes (Chopping/Heating/Grinding/Filtering/Drying/Shaking/Simple Alchemy)...")
    edges = scrape_processing_recipes(session)
    print(f"  {len(edges)} processing recipes scraped.")
    print("Scraping cooking recipes (this fetches one detail page per recipe, ~2-3 minutes)...")
    cooking_edges = scrape_cooking_recipes(session)
    print(f"  {len(cooking_edges)} cooking recipes scraped.")
    print("Scraping alchemy recipes (this fetches one detail page per recipe)...")
    alchemy_edges = scrape_alchemy_recipes(session)
    print(f"  {len(alchemy_edges)} alchemy recipes scraped.")
    return edges + cooking_edges + alchemy_edges
