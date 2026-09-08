import json
import re
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
}

_ICON_ID_RE = re.compile(r"/(\d+)(?:_\d+)?\.webp")


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


def _primary_output_item_id(icon_cell_html: str) -> int | None:
    match = _ICON_ID_RE.search(icon_cell_html)
    return int(match.group(1)) if match else None


def _primary_and_bonus_outputs(
    icon_cell_html: str, output_cell_html: str
) -> tuple[YieldRange | None, list[BonusOutput]]:
    primary_id = _primary_output_item_id(icon_cell_html)
    entries = _extract_item_entries(output_cell_html)
    primary: YieldRange | None = None
    bonus: list[BonusOutput] = []
    for item_id, qty_min, qty_max in entries:
        if item_id == primary_id and primary is None:
            primary = YieldRange(item_id, qty_min, qty_max)
        else:
            bonus.append(BonusOutput(item_id, qty_min, qty_max, chance=None))
    return primary, bonus


def parse_mrecipe_row(row: list) -> ConversionEdge:
    recipe_id = row[0]
    name = BeautifulSoup(row[2], "html.parser").select_one("b").get_text(strip=True)
    process_type = row[3]
    mastery_required = row[4]["sort_value"]
    inputs = tuple((iid, qmin) for iid, qmin, _qmax in _extract_item_entries(row[6]))
    primary, bonus = _primary_and_bonus_outputs(row[1], row[8])
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
