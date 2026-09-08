# BDO Profitability Calculator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Python CLI that ranks the user's BDO worker-empire raw materials (ores, wood, cooking base materials) by profitability — sell raw vs. process/cook, including multi-hop chains — using live `api.arsha.io` market prices and recipe data scraped from `bdocodex.com`.

**Architecture:** A unified conversion-graph model (`ConversionEdge`s connecting item IDs) built from scraped Processing (Chopping/Heating/Grinding/Filtering/Drying/Shaking) and Cooking recipes. A memoized, bottom-up profitability engine walks the graph from every raw material to compute the best action (sell now vs. process N hops) net of market tax and ingredient cost, plus separately-tracked "unknown-rate" bonus/tier-skip upside. Recipe data is scraped once and cached locally; market prices are always fetched fresh.

**Tech Stack:** Python 3.10+, `requests`, `beautifulsoup4`, `rich`, `pyyaml`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-09-08-bdo-profit-calculator-design.md`

## Global Constraints

- Python 3.10+, type hints throughout (per user's global CLAUDE.md).
- Region is hardcoded to EU (`api.arsha.io/v2/eu/...`) — confirmed as the user's game region.
- `bdocodex.com` is the sole recipe/scrape source (garmoth.com is Cloudflare-blocked; bdolytics wasn't needed).
- No live network calls in unit tests — HTTP is always mocked (`unittest.mock`) or driven from local fixture files.
- Files under ~300 lines; split further if a task's file grows past that.
- Commit after every task with a conventional commit message, directly to `master` (no feature branches — solo hobby project, per approved spec).
- **Deviations from the original spec, found during research (call these out to the user after the plan is delivered — do not silently apply them):**
  1. bdocodex does not expose a separate mastery-breakpoint table distinct from the recipe list's own quantity range (e.g. `"1~4"`). That range already spans the game's full mastery spectrum. **v1 uses the midpoint of the scraped `qty_min`–`qty_max` range as the expected base yield**, not a dynamic mastery-driven formula. `config/mastery.yaml` is still seeded with the user's real mastery levels for reference and future refinement, but v1's yield math does not consume it yet.
  2. Cooking "quality tiers" (Simple/Century/etc.) are not evidenced as separate items in the scraped data — dropped from v1's math. What *is* evidenced (and directly matches the user's own example) is that a single recipe row's output cell can list a second item (e.g. `Pickled Vegetables` recipe id 112 lists both `Pickled Vegetables` and `Sour Pickled Vegetables` in one row) — this is modeled as a `BonusOutput` with `chance=None` (unknown), exactly per the agreed "unknown-rate bonus, additive, excluded from core ranking" design.
  3. Bonus/tier-skip outputs are detected directly from a recipe row's own output cell (comparing against the recipe's own icon to find the "primary" item — any other item in that cell is bonus), not via cross-referencing separate linked-recipe IDs — simpler and self-contained, confirmed against real scraped rows.
  4. The spec's separate `Item` model (id/name/sub_id/npc_price) is dropped — recipe data only ever carries item IDs (bdocodex's processing/cooking rows never include item name text, only icons), so items are plain `int`s everywhere in the graph, and display names are sourced for free from `MarketSnapshot.name` (arsha.io already returns the item's name alongside its price).

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `bdo_profit/__init__.py`
- Create: `bdo_profit/__main__.py`
- Create: `tests/__init__.py`
- Test: `tests/test_scaffolding.py`

**Interfaces:**
- Produces: an installable package `bdo_profit` importable from `tests/`, and a `python -m bdo_profit` entrypoint that (for now) just prints a placeholder message — Task 9 replaces the body with the real CLI.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "bdo-profit"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "requests>=2.31",
    "beautifulsoup4>=4.12",
    "rich>=13.7",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["bdo_profit*"]
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.pyc
.venv/
data/recipes_cache.json
```

- [ ] **Step 3: Create package skeleton**

`bdo_profit/__init__.py`:
```python
```

`bdo_profit/__main__.py`:
```python
def main() -> None:
    print("bdo_profit: not yet implemented (see Task 9)")


if __name__ == "__main__":
    main()
```

`tests/__init__.py`:
```python
```

- [ ] **Step 4: Write the failing test**

```python
# tests/test_scaffolding.py
import subprocess
import sys


def test_module_runs():
    result = subprocess.run(
        [sys.executable, "-m", "bdo_profit"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "bdo_profit" in result.stdout
```

- [ ] **Step 5: Create venv, install package, run test**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/test_scaffolding.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore bdo_profit tests
git commit -m "chore: scaffold bdo_profit package"
```

---

## Task 2: Data models

**Files:**
- Create: `bdo_profit/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `YieldRange(item_id: int, qty_min: float, qty_max: float)` with `.expected_qty` property. `BonusOutput(item_id: int, qty_min: float, qty_max: float, chance: float | None)` with `.expected_qty` property. `ConversionEdge(recipe_id: int, name: str, process_type: str, mastery_required: int, inputs: tuple[tuple[int, float], ...], base_outputs: tuple[YieldRange, ...], bonus_outputs: tuple[BonusOutput, ...] = ())`. All frozen dataclasses. These are the types every later task imports from `bdo_profit.models`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_models.py
from bdo_profit.models import YieldRange, BonusOutput, ConversionEdge


def test_yield_range_expected_qty_is_midpoint():
    yr = YieldRange(item_id=4051, qty_min=1, qty_max=4)
    assert yr.expected_qty == 2.5


def test_bonus_output_expected_qty_is_midpoint():
    bo = BonusOutput(item_id=4052, qty_min=1, qty_max=1, chance=None)
    assert bo.expected_qty == 1.0


def test_conversion_edge_defaults_no_bonus():
    edge = ConversionEdge(
        recipe_id=1,
        name="Melted Iron Shard",
        process_type="Heating",
        mastery_required=0,
        inputs=((4001, 5.0),),
        base_outputs=(YieldRange(4051, 1, 4),),
    )
    assert edge.bonus_outputs == ()
    assert edge.inputs == ((4001, 5.0),)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bdo_profit.models'`

- [ ] **Step 3: Write the implementation**

```python
# bdo_profit/models.py
from dataclasses import dataclass, field


@dataclass(frozen=True)
class YieldRange:
    item_id: int
    qty_min: float
    qty_max: float

    @property
    def expected_qty(self) -> float:
        return (self.qty_min + self.qty_max) / 2


@dataclass(frozen=True)
class BonusOutput:
    item_id: int
    qty_min: float
    qty_max: float
    chance: float | None  # None = unknown rate; excluded from core ranking

    @property
    def expected_qty(self) -> float:
        return (self.qty_min + self.qty_max) / 2


@dataclass(frozen=True)
class ConversionEdge:
    recipe_id: int
    name: str
    process_type: str
    mastery_required: int
    inputs: tuple[tuple[int, float], ...]
    base_outputs: tuple[YieldRange, ...]
    bonus_outputs: tuple[BonusOutput, ...] = field(default_factory=tuple)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bdo_profit/models.py tests/test_models.py
git commit -m "feat: add conversion graph data models"
```

---

## Task 3: Config loaders and seed data files

**Files:**
- Create: `bdo_profit/config.py`
- Create: `config/mastery.yaml`
- Create: `config/bonus_proc_rates.yaml`
- Create: `data/npc_prices.json`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_mastery(path: Path) -> MasteryConfig` (dataclass with `gathering_mastery: int`, `processing_mastery: int`, `cooking_mastery: int`, `processing_subskills: dict[str, int]`). `load_npc_prices(path: Path) -> dict[int, float]`. `load_bonus_proc_rates(path: Path) -> dict[int, float]` (only known rates are present as keys; recipe IDs with no entry are treated as unknown by callers).

- [ ] **Step 1: Seed `config/mastery.yaml`** (real values from the user's 2026-09-08 mastery screenshot)

```yaml
gathering:
  mastery: 375
  level: "Artisan 5"
processing:
  mastery: 385
  level: "Artisan 7"
  subskills:
    shaking: 940
    grinding: 940
    chopping: 940
    drying: 940
    filtering: 940
    heating: 940
cooking:
  mastery: 1040
  level: "Master 7"
```

- [ ] **Step 2: Seed `config/bonus_proc_rates.yaml`**

```yaml
# Known bonus/tier-skip proc rates, keyed by bdocodex recipe id, as a fraction (e.g. 0.1 = 10%).
# Empty by default -- rates are unknown until observed in-game. Add entries here to have
# that recipe's bonus output included in the core ranked value instead of shown as upside-only.
```

- [ ] **Step 3: Seed `data/npc_prices.json`**

```json
{}
```

- [ ] **Step 4: Write the failing tests**

```python
# tests/test_config.py
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
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bdo_profit.config'`

- [ ] **Step 6: Write the implementation**

```python
# bdo_profit/config.py
import json
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class MasteryConfig:
    gathering_mastery: int
    processing_mastery: int
    cooking_mastery: int
    processing_subskills: dict[str, int]


def load_mastery(path: Path) -> MasteryConfig:
    data = yaml.safe_load(path.read_text())
    return MasteryConfig(
        gathering_mastery=data["gathering"]["mastery"],
        processing_mastery=data["processing"]["mastery"],
        cooking_mastery=data["cooking"]["mastery"],
        processing_subskills=data["processing"].get("subskills", {}),
    )


def load_npc_prices(path: Path) -> dict[int, float]:
    raw = json.loads(path.read_text())
    return {int(k): float(v) for k, v in raw.items()}


def load_bonus_proc_rates(path: Path) -> dict[int, float]:
    data = yaml.safe_load(path.read_text())
    if not data:
        return {}
    return {int(k): float(v) for k, v in data.items()}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add bdo_profit/config.py config/mastery.yaml config/bonus_proc_rates.yaml data/npc_prices.json tests/test_config.py
git commit -m "feat: add config loaders and seed mastery/NPC price data"
```

---

## Task 4: Market client (arsha.io)

**Files:**
- Create: `bdo_profit/market_client.py`
- Test: `tests/test_market_client.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `MarketSnapshot(item_id: int, sub_id: int, name: str, price: float, current_stock: int, last_sold_price: float, fetched_at: float)`. `MarketClient(region: str = "eu", min_interval: float = 1.0, max_retries: int = 4, cache_ttl: float = 300.0)` with `.get_price(item_id: int, sub_id: int = 0) -> MarketSnapshot | None`. Task 9's CLI is the consumer.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_market_client.py
import time
from unittest.mock import patch, MagicMock

from bdo_profit.market_client import MarketClient


def _mock_response(json_data, status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = text or ""
    return resp


def test_get_price_parses_snapshot():
    client = MarketClient(min_interval=0)
    payload = {
        "name": "Caphras Stone",
        "id": 721003,
        "sid": 0,
        "basePrice": 885000,
        "currentStock": 486322,
        "lastSoldPrice": 820000,
    }
    with patch.object(client._session, "get", return_value=_mock_response(payload)) as get:
        snap = client.get_price(721003)
    assert snap.name == "Caphras Stone"
    assert snap.price == 885000
    assert snap.current_stock == 486322
    get.assert_called_once()
    call_url = get.call_args[0][0]
    assert "GetWorldMarketSubList" in call_url
    assert get.call_args[1]["params"]["id"] == 721003


def test_get_price_caches_within_ttl():
    client = MarketClient(min_interval=0, cache_ttl=300)
    payload = {"name": "X", "id": 1, "sid": 0, "basePrice": 100, "currentStock": 1, "lastSoldPrice": 100}
    with patch.object(client._session, "get", return_value=_mock_response(payload)) as get:
        client.get_price(1)
        client.get_price(1)
    assert get.call_count == 1


def test_get_price_retries_on_imperva_block():
    client = MarketClient(min_interval=0, max_retries=3)
    blocked = _mock_response({}, status_code=500, text='{"code":103,"message":"Imperva"}')
    ok_payload = {"name": "X", "id": 1, "sid": 0, "basePrice": 100, "currentStock": 1, "lastSoldPrice": 100}
    ok = _mock_response(ok_payload)
    with patch.object(client._session, "get", side_effect=[blocked, ok]) as get, \
         patch("time.sleep", return_value=None):
        snap = client.get_price(1)
    assert snap.price == 100
    assert get.call_count == 2


def test_get_price_falls_back_to_stale_cache_on_persistent_failure():
    client = MarketClient(min_interval=0, cache_ttl=0.01, max_retries=1)
    ok_payload = {"name": "X", "id": 1, "sid": 0, "basePrice": 100, "currentStock": 1, "lastSoldPrice": 100}
    with patch.object(client._session, "get", return_value=_mock_response(ok_payload)):
        client.get_price(1)
    time.sleep(0.02)
    blocked = _mock_response({}, status_code=500, text='{"code":103}')
    with patch.object(client._session, "get", return_value=blocked), \
         patch("time.sleep", return_value=None):
        snap = client.get_price(1)
    assert snap is not None
    assert snap.price == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_market_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bdo_profit.market_client'`

- [ ] **Step 3: Write the implementation**

```python
# bdo_profit/market_client.py
import time
from dataclasses import dataclass

import requests

ARSHA_BASE = "https://api.arsha.io/v2"


@dataclass
class MarketSnapshot:
    item_id: int
    sub_id: int
    name: str
    price: float
    current_stock: int
    last_sold_price: float
    fetched_at: float


class MarketClient:
    def __init__(
        self,
        region: str = "eu",
        min_interval: float = 1.0,
        max_retries: int = 4,
        cache_ttl: float = 300.0,
    ):
        self.region = region
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.cache_ttl = cache_ttl
        self._session = requests.Session()
        self._last_request_time = 0.0
        self._cache: dict[tuple[int, int], MarketSnapshot] = {}

    def get_price(self, item_id: int, sub_id: int = 0) -> MarketSnapshot | None:
        key = (item_id, sub_id)
        cached = self._cache.get(key)
        now = time.time()
        if cached and (now - cached.fetched_at) < self.cache_ttl:
            return cached

        data = self._get_with_retry(
            "GetWorldMarketSubList", {"id": item_id, "sid": sub_id, "lang": "en"}
        )
        if data is None:
            return cached  # stale fallback if we have one, else None

        snapshot = MarketSnapshot(
            item_id=item_id,
            sub_id=sub_id,
            name=str(data.get("name", f"Item {item_id}")),
            price=float(data.get("basePrice", 0) or 0),
            current_stock=int(data.get("currentStock", 0) or 0),
            last_sold_price=float(data.get("lastSoldPrice", 0) or 0),
            fetched_at=now,
        )
        self._cache[key] = snapshot
        return snapshot

    def _get_with_retry(self, endpoint: str, params: dict) -> dict | None:
        url = f"{ARSHA_BASE}/{self.region}/{endpoint}"
        for attempt in range(self.max_retries):
            self._respect_rate_limit()
            try:
                resp = self._session.get(url, params=params, timeout=15)
            except requests.RequestException:
                self._last_request_time = time.time()
                self._backoff(attempt)
                continue
            self._last_request_time = time.time()
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    return None
            if resp.status_code == 500 and "Imperva" in (resp.text or ""):
                self._backoff(attempt)
                continue
            return None
        return None

    def _respect_rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def _backoff(self, attempt: int) -> None:
        time.sleep(min(2**attempt, 30))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_market_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bdo_profit/market_client.py tests/test_market_client.py
git commit -m "feat: add rate-limited arsha.io market client with retry and TTL cache"
```

---

## Task 5: bdocodex processing-chain scraper

**Files:**
- Create: `bdo_profit/scraper/__init__.py`
- Create: `bdo_profit/scraper/bdocodex.py`
- Create: `tests/fixtures/mrecipes_heating_sample.json`
- Test: `tests/test_bdocodex_processing.py`

**Interfaces:**
- Consumes: `ConversionEdge`, `YieldRange`, `BonusOutput` from `bdo_profit.models`.
- Produces: `PROCESS_TYPES: dict[str, str]` (bdocodex URL slug -> display name, e.g. `"heating": "Heating"`). `fetch_mrecipes_json(process_slug: str, session: requests.Session) -> list[list]` (raw `aaData` rows). `parse_mrecipe_row(row: list) -> ConversionEdge`. `scrape_processing_recipes(session: requests.Session) -> list[ConversionEdge]`. Task 6 reuses the shared `_extract_item_entries` and `_primary_and_bonus_outputs` helpers this task defines; Task 7 consumes `scrape_processing_recipes`.

This is grounded directly in real bdocodex responses fetched during design research (`https://bdocodex.com/query.php?a=mrecipes&type=heating&id=1&l=us`, confirmed 200 OK, `aaData` array of rows). Row shape confirmed by inspection:
`[recipe_id, icon_cell_html, name_cell_html, process_type, {"sort_value": int, ...}, unused, inputs_cell_html, unused_float, outputs_cell_html, [...], [...], int, int]`.
Inputs/outputs cells contain `<div class="iconset_wrapper_medium"><a data-id="item--{id}">...<div class="quantity_small">{qty or "min~max"}</div></a></div>` per item. The recipe's own icon cell contains a `.../{item_id}.webp` (or `{item_id}_N.webp`) path identifying which output item is the "primary" (guaranteed) one; any other item present in the outputs cell is a bonus/tier-skip output with unknown chance.

- [ ] **Step 1: Create the fixture** (trimmed, real data — rows 1-3 from the live `type=heating` response)

```json
{"aaData": [
[1, "<div class=\"iconset_wrapper_big\"><a href=\"/us/mrecipe/1/\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004051.webp\" class=\"list_icon_big qtooltip\" data-id=\"mrecipe--1\" alt=\"icon\"]</div></a></div>", "<a href=\"/us/mrecipe/1/\" class=\"qtooltip item_grade_0\" data-id=\"mrecipe--1\" data-enchant=\"0\"><b><span></span>Melted Iron Shard</b></a>", "Heating", {"display": "Beginner 0", "sort_value": 0}, "", "<div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/4001/\" class=\"qtooltip\" data-id=\"item--4001\" data-enchant=\"\" data-tiptype=\"recipe\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004001.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">5</div></a></div>", "1.50", "<div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/4051/\" class=\"qtooltip\" data-id=\"item--4051\" data-enchant=\"\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004051.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">1~4</div></a></div><div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/4052/\" class=\"qtooltip\" data-id=\"item--4052\" data-enchant=\"\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004052.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">1</div></a></div>", "[4001]", "[]", 7, 0],
[2, "<div class=\"iconset_wrapper_big\"><a href=\"/us/mrecipe/2/\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004052.webp\" class=\"list_icon_big qtooltip\" data-id=\"mrecipe--2\" alt=\"icon\"]</div></a></div>", "<a href=\"/us/mrecipe/2/\" class=\"qtooltip item_grade_0\" data-id=\"mrecipe--2\" data-enchant=\"0\"><b><span></span>Iron Ingot</b></a>", "Heating", {"display": "Beginner 0", "sort_value": 0}, "", "<div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/4051/\" class=\"qtooltip\" data-id=\"item--4051\" data-enchant=\"\" data-tiptype=\"recipe\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004051.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">10</div></a></div>", "3.00", "<div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/4052/\" class=\"qtooltip\" data-id=\"item--4052\" data-enchant=\"\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004052.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">1~4</div></a></div>", "[4051]", "[]", 7, 0],
[3, "<div class=\"iconset_wrapper_big\"><a href=\"/us/mrecipe/3/\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004054.webp\" class=\"list_icon_big qtooltip\" data-id=\"mrecipe--3\" alt=\"icon\"]</div></a></div>", "<a href=\"/us/mrecipe/3/\" class=\"qtooltip item_grade_0\" data-id=\"mrecipe--3\" data-enchant=\"0\"><b><span></span>Melted Lead Shard</b></a>", "Heating", {"display": "Beginner 0", "sort_value": 0}, "", "<div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/4002/\" class=\"qtooltip\" data-id=\"item--4002\" data-enchant=\"\" data-tiptype=\"recipe\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004002.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">5</div></a></div>", "1.50", "<div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/4054/\" class=\"qtooltip\" data-id=\"item--4054\" data-enchant=\"\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004054.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">1~4</div></a></div><div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/4055/\" class=\"qtooltip\" data-id=\"item--4055\" data-enchant=\"\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00004055.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">1</div></a></div>", "[4002]", "[]", 7, 0]
]}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_bdocodex_processing.py
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
    ), patch("time.sleep", return_value=None):
        edges = scrape_processing_recipes(MagicMock())

    assert len(edges) == 1
    assert edges[0].recipe_id == 1
    assert "WARNING" in capsys.readouterr().out
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_bdocodex_processing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bdo_profit.scraper'`

- [ ] **Step 4: Write the implementation**

`bdo_profit/scraper/__init__.py`:
```python
```

`bdo_profit/scraper/bdocodex.py`:
```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_bdocodex_processing.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add bdo_profit/scraper tests/test_bdocodex_processing.py tests/fixtures/mrecipes_heating_sample.json
git commit -m "feat: scrape bdocodex processing (Chopping/Heating/etc) recipes"
```

---

## Task 6: bdocodex cooking-recipe scraper

**Files:**
- Modify: `bdo_profit/scraper/bdocodex.py`
- Create: `tests/fixtures/recipes_culinary_sample.json`
- Create: `tests/fixtures/recipe_106_detail_sample.html`
- Test: `tests/test_bdocodex_cooking.py`

**Interfaces:**
- Consumes: `_extract_item_entries`, `_primary_and_bonus_outputs` from Task 5 (same file).
- Produces: `fetch_recipes_json(recipe_type: str, session: requests.Session) -> list[list]`. `parse_cooking_summary_row(row: list) -> dict` (keys: `recipe_id`, `name`, `mastery_required`, `primary_output: YieldRange | None`, `bonus_outputs: list[BonusOutput]`). `fetch_recipe_ingredients(recipe_id: int, session: requests.Session) -> tuple[tuple[int, float], ...]`. `build_cooking_edge(summary: dict, ingredients: tuple[tuple[int, float], ...]) -> ConversionEdge`. `scrape_cooking_recipes(session: requests.Session) -> list[ConversionEdge]`. Task 7 consumes `scrape_cooking_recipes` alongside `scrape_processing_recipes`.

Grounded in real bdocodex responses: recipe listing at `https://bdocodex.com/query.php?a=recipes&type=culinary&id=1&l=us` (confirmed 200 OK, same `aaData` shape as processing but columns 6/7 are a single "key" ingredient + opaque float, column 8 is the output cell — which, like processing, can hold more than one item, e.g. recipe 112 "Pickled Vegetables" lists both item 9202 (qty 1~4, primary) and item 9281 (qty 1~2, bonus) in the same cell). The full multi-ingredient list isn't in the list view — it's scraped from each recipe's own detail page `https://bdocodex.com/us/recipe/{id}/`, which has a static (non-AJAX) tooltip table with a `<span class="yellow_text">- Crafting Materials</span>` label followed by the same `div.iconset_wrapper_medium` item markup used elsewhere.

- [ ] **Step 1: Create the fixtures** (trimmed, real data)

`tests/fixtures/recipes_culinary_sample.json`:
```json
{"aaData": [
[106, "<div class=\"iconset_wrapper_big\"><a href=\"/us/recipe/106/\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00009003.webp\" class=\"list_icon_big qtooltip\" data-id=\"recipe--106\" alt=\"icon\"]</div></a></div>", "<div class=\"form-check list_checkbox inlinediv qtooltip\" title=\"Login to use favorites\" data-recipe_id=\"106\"><input type=\"checkbox\" class=\"form-check-input pointer fav_checkbox\" id=\"fav_recipe_106\" name=\"fav_recipe_106\" disabled><label class=\"form-check-label\" for=\"fav_recipe_106\"></label></div><a href=\"/us/recipe/106/\" class=\"qtooltip item_grade_0\" data-id=\"recipe--106\"><b>White Sauce</b></a>", "Cooking", {"display": "Beginner 1", "sort_value": 1}, "400", "<div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/9018/\" class=\"qtooltip\" data-id=\"item--9018\" data-enchant=\"\" data-tiptype=\"recipekey\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00009018.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">1</div></a></div>", "0.14", "<div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/9003/\" class=\"qtooltip\" data-id=\"item--9003\" data-enchant=\"\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00009003.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">1~4</div></a></div>", "[9018,9065,7313,9017]", "[]", null, 0],
[112, "<div class=\"iconset_wrapper_big\"><a href=\"/us/recipe/112/\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00009202.webp\" class=\"list_icon_big qtooltip\" data-id=\"recipe--112\" alt=\"icon\"]</div></a></div>", "<div class=\"form-check list_checkbox inlinediv qtooltip\" title=\"Login to use favorites\" data-recipe_id=\"112\"><input type=\"checkbox\" class=\"form-check-input pointer fav_checkbox\" id=\"fav_recipe_112\" name=\"fav_recipe_112\" disabled><label class=\"form-check-label\" for=\"fav_recipe_112\"></label></div><a href=\"/us/recipe/112/\" class=\"qtooltip item_grade_1\" data-id=\"recipe--112\"><b>Pickled Vegetables</b></a>", "Cooking", {"display": "Apprentice 1", "sort_value": 11}, "700", "<div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/9066/\" class=\"qtooltip\" data-id=\"item--9066\" data-enchant=\"\" data-tiptype=\"recipekey\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00009066.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_0\"]</div><div class=\"quantity_small nowrap\">4</div></a></div>", "0.88", "<div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/9202/\" class=\"qtooltip\" data-id=\"item--9202\" data-enchant=\"\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00009202.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_1\"]</div><div class=\"quantity_small nowrap\">1~4</div></a></div><div class=\"iconset_wrapper_medium inlinediv\"><a href=\"/us/item/9281/\" class=\"qtooltip\" data-id=\"item--9281\" data-enchant=\"\"><div class=\"icon_wrapper\">[img src=\"/items/new_icon/03_etc/07_productmaterial/00009281.webp\" alt=\"icon\" class=\"list_icon_medium grade_frame_2\"]</div><div class=\"quantity_small nowrap\">1~2</div></a></div>", "[7318,9066,9005,9002]", "[31]", null, 0]
]}
```

`tests/fixtures/recipe_106_detail_sample.html` (trimmed real markup from `/us/recipe/106/`):
```html
<table><tr><td colspan="2"><span class="yellow_text">- Crafting Materials</span><br>
<div class="iconset_wrapper_medium inlinediv"><a href="/us/item/9018/" class="qtooltip" data-id="item--9018" data-enchant=""><div class="icon_wrapper"><img src="/items/new_icon/03_etc/07_productmaterial/00009018.webp" alt="icon" class="list_icon_medium grade_frame_0"></div><div class="quantity_small nowrap">1</div><img src="/images/icon_lock.webp" class="sub_icon_rt" width="16" height="16" alt="lock"></a></div> - <a href="/us/item/9018/" class="qtooltip item_grade_0" data-id="item--9018" data-enchant="">Base Sauce</a><br>
<div class="iconset_wrapper_medium inlinediv"><a href="/us/item/9065/" class="qtooltip" data-id="item--9065" data-enchant=""><div class="icon_wrapper"><img src="/items/new_icon/03_etc/07_productmaterial/00009065.webp" alt="icon" class="list_icon_medium grade_frame_0"></div><div class="quantity_small nowrap">1</div></a></div> - <a href="/us/item/9065/" class="qtooltip item_grade_0" data-id="item--9065" data-enchant="">Milk</a><br>
<div class="iconset_wrapper_medium inlinediv"><a href="/us/item/7313/" class="qtooltip" data-id="item--7313" data-enchant="" data-tiptype="recipe"><div class="icon_wrapper"><img src="/items/new_icon/03_etc/07_productmaterial/00007313.webp" alt="icon" class="list_icon_medium grade_frame_0"></div><div class="quantity_small nowrap">1</div><img src="/images/icon-repeat.webp" class="sub_icon_rt bg-white" width="14" height="14" alt="group"></a></div> - <a href="/us/item/7313/" class="qtooltip item_grade_0" data-id="item--7313" data-enchant="" data-tiptype="recipe">Apple</a><br>
<div class="iconset_wrapper_medium inlinediv"><a href="/us/item/9017/" class="qtooltip" data-id="item--9017" data-enchant=""><div class="icon_wrapper"><img src="/items/new_icon/03_etc/07_productmaterial/00009017.webp" alt="icon" class="list_icon_medium grade_frame_0"></div><div class="quantity_small nowrap">2</div></a></div> - <a href="/us/item/9017/" class="qtooltip item_grade_0" data-id="item--9017" data-enchant="">Cooking Wine</a>
</td></tr>
<tr><td colspan="2"><span class="yellow_text">- Crafting Result</span><br>
<div class="iconset_wrapper_medium inlinediv"><a href="/us/item/9003/" class="qtooltip" data-id="item--9003" data-enchant=""><div class="icon_wrapper"><img src="/items/new_icon/03_etc/07_productmaterial/00009003.webp" alt="icon" class="list_icon_medium grade_frame_0"></div><div class="quantity_small nowrap">1~4</div></a></div>
</td></tr></table>
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_bdocodex_cooking.py
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_bdocodex_cooking.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_cooking_summary_row'`

- [ ] **Step 4: Extend the implementation** — append to `bdo_profit/scraper/bdocodex.py`

```python
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
    primary, bonus = _primary_and_bonus_outputs(row[1], row[8])
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
```

Note: `_extract_item_entries` is called with `str(container)` (re-serializing the found `<td>` back to HTML) so it can reuse the exact same parsing helper as Task 5 without duplicating logic — it already only looks for `div.iconset_wrapper_medium` regardless of surrounding markup, so it correctly ignores the "Crafting Result" row when scoped to the "Crafting Materials" `<td>` only.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_bdocodex_cooking.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add bdo_profit/scraper/bdocodex.py tests/test_bdocodex_cooking.py tests/fixtures/recipes_culinary_sample.json tests/fixtures/recipe_106_detail_sample.html
git commit -m "feat: scrape bdocodex cooking recipes and ingredient details"
```

---

## Task 7: Recipe cache (save/load) and combined scrape

**Files:**
- Create: `bdo_profit/cache.py`
- Modify: `bdo_profit/scraper/bdocodex.py` (add `scrape_all`)
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `ConversionEdge`, `YieldRange`, `BonusOutput` from `bdo_profit.models`; `scrape_processing_recipes`, `scrape_cooking_recipes` from `bdo_profit.scraper.bdocodex`.
- Produces: `save_edges(edges: list[ConversionEdge], path: Path) -> None`. `load_edges(path: Path) -> tuple[list[ConversionEdge], float]` (edges, `scraped_at` unix timestamp). `scrape_all(session: requests.Session | None = None) -> list[ConversionEdge]`. Task 9 (CLI) consumes all three.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cache.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bdo_profit.cache'`

- [ ] **Step 3: Write the implementation**

```python
# bdo_profit/cache.py
import json
import time
from dataclasses import asdict
from pathlib import Path

from bdo_profit.models import BonusOutput, ConversionEdge, YieldRange


def save_edges(edges: list[ConversionEdge], path: Path) -> None:
    payload = {
        "scraped_at": time.time(),
        "edges": [asdict(e) for e in edges],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def load_edges(path: Path) -> tuple[list[ConversionEdge], float]:
    payload = json.loads(path.read_text())
    edges = []
    for e in payload["edges"]:
        base_outputs = tuple(YieldRange(**yr) for yr in e["base_outputs"])
        bonus_outputs = tuple(BonusOutput(**bo) for bo in e["bonus_outputs"])
        edges.append(
            ConversionEdge(
                recipe_id=e["recipe_id"],
                name=e["name"],
                process_type=e["process_type"],
                mastery_required=e["mastery_required"],
                inputs=tuple(tuple(pair) for pair in e["inputs"]),
                base_outputs=base_outputs,
                bonus_outputs=bonus_outputs,
            )
        )
    return edges, payload["scraped_at"]
```

Append to `bdo_profit/scraper/bdocodex.py`:
```python
def scrape_all(session: requests.Session | None = None) -> list[ConversionEdge]:
    session = session or requests.Session()
    print("Scraping processing recipes (Chopping/Heating/Grinding/Filtering/Drying/Shaking)...")
    edges = scrape_processing_recipes(session)
    print(f"  {len(edges)} processing recipes scraped.")
    print("Scraping cooking recipes (this fetches one detail page per recipe, ~2-3 minutes)...")
    cooking_edges = scrape_cooking_recipes(session)
    print(f"  {len(cooking_edges)} cooking recipes scraped.")
    return edges + cooking_edges
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bdo_profit/cache.py bdo_profit/scraper/bdocodex.py tests/test_cache.py
git commit -m "feat: add recipe cache persistence and combined scrape entrypoint"
```

---

## Task 8: Profitability engine

**Files:**
- Create: `bdo_profit/profitability.py`
- Test: `tests/test_profitability.py`

**Interfaces:**
- Consumes: `ConversionEdge`, `YieldRange`, `BonusOutput` from `bdo_profit.models`.
- Produces: `PathResult(item_id: int, action: str, value_per_unit: float, steps: tuple[str, ...], process_types: tuple[str, ...], bonus_upside_per_unit: float = 0.0)`. `build_edges_by_input(edges: list[ConversionEdge]) -> dict[int, list[ConversionEdge]]`. `identify_raw_materials(edges: list[ConversionEdge]) -> set[int]`. `all_item_ids(edges: list[ConversionEdge]) -> set[int]`. `acquisition_cost(item_id: int, prices: dict[int, float], npc_prices: dict[int, float]) -> float`. `apply_bonus_rate_overrides(edges: list[ConversionEdge], rates: dict[int, float]) -> list[ConversionEdge]`. `best_path_value(item_id: int, edges_by_input: dict[int, list[ConversionEdge]], prices: dict[int, float], npc_prices: dict[int, float], tax_rate: float, memo: dict[int, PathResult], visiting: frozenset[int] = frozenset()) -> PathResult`. Task 9 (CLI) consumes all of these.

Cost model: `prices[item_id]` is the raw market listed price (used directly as **buy cost** for secondary ingredients via `acquisition_cost`, no tax). Selling a unit nets `price * tax_rate` (tax only applies when selling, per BDO's central market — a single listed price is shared by buyers and sellers, sellers just receive less). `best_path_value` decides, per item, whether selling now or feeding it into the best available edge is worth more; it is memoized and recurses **through the graph in the direction of outputs**, guarded against cycles via `visiting`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_profitability.py
from bdo_profit.models import BonusOutput, ConversionEdge, YieldRange
from bdo_profit.profitability import (
    acquisition_cost,
    all_item_ids,
    apply_bonus_rate_overrides,
    best_path_value,
    build_edges_by_input,
    identify_raw_materials,
)

# Toy chain: raw Log (1) --Chopping--> Plank (2) --Chopping--> Square Timber (3)
# Plank chopping also has an unknown-rate bonus output of Square Timber (tier-skip).
LOG_TO_PLANK = ConversionEdge(
    recipe_id=1,
    name="Plank",
    process_type="Chopping",
    mastery_required=0,
    inputs=((1, 10.0),),
    base_outputs=(YieldRange(2, 8.0, 8.0),),
    bonus_outputs=(BonusOutput(3, 1.0, 1.0, chance=None),),
)
PLANK_TO_TIMBER = ConversionEdge(
    recipe_id=2,
    name="Square Timber",
    process_type="Chopping",
    mastery_required=0,
    inputs=((2, 5.0),),
    base_outputs=(YieldRange(3, 1.0, 1.0),),
)


def test_identify_raw_materials():
    raw = identify_raw_materials([LOG_TO_PLANK, PLANK_TO_TIMBER])
    assert raw == {1}


def test_all_item_ids():
    ids = all_item_ids([LOG_TO_PLANK, PLANK_TO_TIMBER])
    assert ids == {1, 2, 3}


def test_acquisition_cost_prefers_cheaper_of_market_and_npc():
    prices = {5000: 20.0}
    npc = {5000: 14.0}
    assert acquisition_cost(5000, prices, npc) == 14.0


def test_acquisition_cost_falls_back_to_whichever_exists():
    assert acquisition_cost(1, {1: 100.0}, {}) == 100.0
    assert acquisition_cost(1, {}, {1: 50.0}) == 50.0


def test_sell_raw_when_no_processing_beats_selling():
    edges_by_input = build_edges_by_input([])
    prices = {1: 100.0}
    result = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    assert result.action == "sell_raw"
    assert result.value_per_unit == 65.0


def test_prefers_processing_when_it_pays_more_than_raw_sale():
    edges_by_input = build_edges_by_input([LOG_TO_PLANK, PLANK_TO_TIMBER])
    # Log sells for 10, Plank sells for 200 -> processing 10 logs into 8 planks
    # nets (8 * 200 * 0.65) / 10 = 104/unit, way more than selling raw at 6.5/unit.
    prices = {1: 10.0, 2: 200.0, 3: 5000.0}
    result = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    assert result.action != "sell_raw"
    assert result.value_per_unit > 6.5


def test_prefers_raw_sale_when_processing_loses_value():
    edges_by_input = build_edges_by_input([LOG_TO_PLANK, PLANK_TO_TIMBER])
    # Log sells for 1000, Plank/Timber are nearly worthless -> raw sale wins.
    prices = {1: 1000.0, 2: 1.0, 3: 1.0}
    result = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    assert result.action == "sell_raw"
    assert result.value_per_unit == 650.0


def test_unknown_bonus_output_tracked_as_upside_not_core_value():
    edges_by_input = build_edges_by_input([LOG_TO_PLANK, PLANK_TO_TIMBER])
    prices = {1: 10.0, 2: 200.0, 3: 5000.0}
    without_bonus = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    assert without_bonus.bonus_upside_per_unit > 0
    # bonus is a Square Timber tier-skip worth a lot -- confirm it's additive upside, not core.
    core_only = without_bonus.value_per_unit - without_bonus.bonus_upside_per_unit
    assert core_only < without_bonus.value_per_unit


def test_apply_bonus_rate_overrides_sets_known_chance():
    updated = apply_bonus_rate_overrides([LOG_TO_PLANK], {1: 0.1})
    assert updated[0].bonus_outputs[0].chance == 0.1
    # original object is untouched (edges are frozen/immutable)
    assert LOG_TO_PLANK.bonus_outputs[0].chance is None


def test_multi_input_edge_uses_acquisition_cost_for_secondary_ingredients():
    # Cooking-style recipe: target item (1) + a bought ingredient (99) -> dish (2)
    recipe = ConversionEdge(
        recipe_id=9,
        name="Dish",
        process_type="Cooking",
        mastery_required=0,
        inputs=((1, 1.0), (99, 2.0)),
        base_outputs=(YieldRange(2, 1.0, 1.0),),
    )
    edges_by_input = build_edges_by_input([recipe])
    prices = {1: 10.0, 2: 1000.0, 99: 50.0}
    result = best_path_value(1, edges_by_input, prices, {}, tax_rate=0.65, memo={})
    # net = (1000*0.65) - (50*2) = 550; value_per_unit = 550 / 1 (qty of item 1 in recipe)
    assert result.value_per_unit == 550.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_profitability.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bdo_profit.profitability'`

- [ ] **Step 3: Write the implementation**

```python
# bdo_profit/profitability.py
from dataclasses import dataclass

from bdo_profit.models import BonusOutput, ConversionEdge


@dataclass
class PathResult:
    item_id: int
    action: str
    value_per_unit: float
    steps: tuple[str, ...]
    process_types: tuple[str, ...]
    bonus_upside_per_unit: float = 0.0


def build_edges_by_input(edges: list[ConversionEdge]) -> dict[int, list[ConversionEdge]]:
    graph: dict[int, list[ConversionEdge]] = {}
    for edge in edges:
        for item_id, _qty in edge.inputs:
            graph.setdefault(item_id, []).append(edge)
    return graph


def identify_raw_materials(edges: list[ConversionEdge]) -> set[int]:
    all_inputs = {iid for e in edges for iid, _ in e.inputs}
    all_outputs = {yr.item_id for e in edges for yr in e.base_outputs}
    return all_inputs - all_outputs


def all_item_ids(edges: list[ConversionEdge]) -> set[int]:
    ids: set[int] = set()
    for e in edges:
        ids.update(iid for iid, _ in e.inputs)
        ids.update(o.item_id for o in e.base_outputs)
        ids.update(b.item_id for b in e.bonus_outputs)
    return ids


def acquisition_cost(
    item_id: int, prices: dict[int, float], npc_prices: dict[int, float]
) -> float:
    candidates = [
        c for c in (prices.get(item_id), npc_prices.get(item_id)) if c is not None
    ]
    return min(candidates) if candidates else float("inf")


def apply_bonus_rate_overrides(
    edges: list[ConversionEdge], rates: dict[int, float]
) -> list[ConversionEdge]:
    updated = []
    for e in edges:
        rate = rates.get(e.recipe_id)
        if rate is None or not e.bonus_outputs:
            updated.append(e)
            continue
        new_bonus = tuple(
            BonusOutput(b.item_id, b.qty_min, b.qty_max, chance=rate) for b in e.bonus_outputs
        )
        updated.append(
            ConversionEdge(
                e.recipe_id,
                e.name,
                e.process_type,
                e.mastery_required,
                e.inputs,
                e.base_outputs,
                new_bonus,
            )
        )
    return updated


def best_path_value(
    item_id: int,
    edges_by_input: dict[int, list[ConversionEdge]],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    memo: dict[int, PathResult],
    visiting: frozenset[int] = frozenset(),
) -> PathResult:
    if item_id in memo:
        return memo[item_id]

    sell_value = prices.get(item_id, 0.0) * tax_rate
    best = PathResult(item_id, "sell_raw", sell_value, (), ())

    if item_id not in visiting:
        next_visiting = visiting | {item_id}
        for edge in edges_by_input.get(item_id, []):
            candidate = _evaluate_edge(
                edge, item_id, edges_by_input, prices, npc_prices, tax_rate, memo, next_visiting
            )
            if candidate is not None and candidate.value_per_unit > best.value_per_unit:
                best = candidate

    memo[item_id] = best
    return best


def _evaluate_edge(
    edge: ConversionEdge,
    target_item_id: int,
    edges_by_input: dict[int, list[ConversionEdge]],
    prices: dict[int, float],
    npc_prices: dict[int, float],
    tax_rate: float,
    memo: dict[int, PathResult],
    visiting: frozenset[int],
) -> PathResult | None:
    target_qty = next((qty for iid, qty in edge.inputs if iid == target_item_id), None)
    if not target_qty:
        return None

    other_cost = sum(
        acquisition_cost(iid, prices, npc_prices) * qty
        for iid, qty in edge.inputs
        if iid != target_item_id
    )

    output_value = 0.0
    downstream_steps: tuple[str, ...] = ()
    downstream_types: tuple[str, ...] = ()
    for i, out in enumerate(edge.base_outputs):
        downstream = best_path_value(
            out.item_id, edges_by_input, prices, npc_prices, tax_rate, memo, visiting
        )
        output_value += out.expected_qty * downstream.value_per_unit
        if i == 0 and downstream.action != "sell_raw":
            downstream_steps = downstream.steps
            downstream_types = downstream.process_types

    known_bonus_value = 0.0
    unknown_bonus_upside = 0.0
    for bonus in edge.bonus_outputs:
        bonus_unit_value = best_path_value(
            bonus.item_id, edges_by_input, prices, npc_prices, tax_rate, memo, visiting
        ).value_per_unit
        if bonus.chance is None:
            unknown_bonus_upside += bonus.expected_qty * bonus_unit_value
        else:
            known_bonus_value += bonus.expected_qty * bonus.chance * bonus_unit_value

    net_per_batch = output_value - other_cost + known_bonus_value
    value_per_unit = net_per_batch / target_qty
    bonus_upside_per_unit = unknown_bonus_upside / target_qty

    action = f"{edge.process_type}: {edge.name}"
    if downstream_steps:
        action += " -> " + " -> ".join(downstream_steps)

    return PathResult(
        item_id=target_item_id,
        action=action,
        value_per_unit=value_per_unit,
        steps=(edge.name,) + downstream_steps,
        process_types=(edge.process_type,) + downstream_types,
        bonus_upside_per_unit=bonus_upside_per_unit,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_profitability.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bdo_profit/profitability.py tests/test_profitability.py
git commit -m "feat: add mastery-range-aware profitability engine with bonus upside tracking"
```

---

## Task 9: CLI wiring and manual verification

**Files:**
- Modify: `bdo_profit/__main__.py`
- Create: `bdo_profit/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 2-8.
- Produces: `main(argv: list[str] | None = None) -> None`, the full CLI entrypoint. Nothing downstream consumes this — it's the top of the stack.

- [ ] **Step 1: Write the failing test** (integration-style: fake edges + fake price lookup, no live network or scraping)

```python
# tests/test_cli.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bdo_profit.cli'`

- [ ] **Step 3: Write the implementation**

```python
# bdo_profit/cli.py
import argparse
import csv as csv_module
from pathlib import Path

from bdo_profit import cache, config
from bdo_profit.market_client import MarketClient
from bdo_profit.profitability import (
    PathResult,
    all_item_ids,
    apply_bonus_rate_overrides,
    best_path_value,
    build_edges_by_input,
    identify_raw_materials,
)
from bdo_profit.scraper.bdocodex import scrape_all

CATEGORY_PROCESS_TYPES = {
    "ore": {"Heating"},
    "wood": {"Chopping"},
    "cooking": {"Cooking"},
}

DEFAULT_CACHE_PATH = Path("data/recipes_cache.json")
DEFAULT_MASTERY_PATH = Path("config/mastery.yaml")
DEFAULT_BONUS_PATH = Path("config/bonus_proc_rates.yaml")
DEFAULT_NPC_PATH = Path("data/npc_prices.json")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bdo_profit")
    parser.add_argument("--category", choices=["ore", "wood", "cooking", "all"], default="all")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--refresh-recipes", action="store_true")
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--tax-rate", type=float, default=0.65)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--mastery-file", type=Path, default=DEFAULT_MASTERY_PATH)
    parser.add_argument("--bonus-rates-path", type=Path, default=DEFAULT_BONUS_PATH)
    parser.add_argument("--npc-prices-path", type=Path, default=DEFAULT_NPC_PATH)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)

    if args.refresh_recipes or not args.cache_path.exists():
        edges = scrape_all()
        cache.save_edges(edges, args.cache_path)
    else:
        edges, _scraped_at = cache.load_edges(args.cache_path)

    npc_prices = config.load_npc_prices(args.npc_prices_path)
    bonus_rates = config.load_bonus_proc_rates(args.bonus_rates_path)
    edges = apply_bonus_rate_overrides(edges, bonus_rates)

    raw_material_ids = identify_raw_materials(edges)
    edges_by_input = build_edges_by_input(edges)

    client = MarketClient()
    prices: dict[int, float] = {}
    names: dict[int, str] = {}
    for item_id in all_item_ids(edges):
        snapshot = client.get_price(item_id)
        if snapshot:
            prices[item_id] = snapshot.price
            names[item_id] = snapshot.name

    memo: dict[int, PathResult] = {}
    results = [
        best_path_value(item_id, edges_by_input, prices, npc_prices, args.tax_rate, memo)
        for item_id in raw_material_ids
    ]

    if args.category != "all":
        allowed = CATEGORY_PROCESS_TYPES[args.category]
        results = [r for r in results if set(r.process_types) & allowed]

    results.sort(key=lambda r: r.value_per_unit, reverse=True)
    results = results[: args.top]

    _print_table(results, names, args.category)

    if args.csv:
        _write_csv(results, names, args.csv)


def _print_table(results: list[PathResult], names: dict[int, str], category: str) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title=f"BDO Profitability ({category})")
    table.add_column("Item")
    table.add_column("Best Action")
    table.add_column("Value/unit", justify="right")
    table.add_column("Bonus upside/unit", justify="right")
    for r in results:
        table.add_row(
            names.get(r.item_id, f"Item {r.item_id}"),
            r.action,
            f"{r.value_per_unit:,.0f}",
            f"+{r.bonus_upside_per_unit:,.0f}",
        )
    Console().print(table)


def _write_csv(results: list[PathResult], names: dict[int, str], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv_module.writer(f)
        writer.writerow(["item", "best_action", "value_per_unit", "bonus_upside_per_unit"])
        for r in results:
            writer.writerow(
                [
                    names.get(r.item_id, f"Item {r.item_id}"),
                    r.action,
                    r.value_per_unit,
                    r.bonus_upside_per_unit,
                ]
            )
```

Replace `bdo_profit/__main__.py`:
```python
from bdo_profit.cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests PASS (Tasks 1-9)

- [ ] **Step 6: Manual end-to-end verification against the real, live site**

```bash
python -m bdo_profit --refresh-recipes --category ore --top 15
```
Expected: prints progress lines while scraping bdocodex (a few minutes, mostly spent on cooking recipe detail pages), then a `rich` table of the top 15 ore-related raw materials ranked by value/unit, pulling live EU prices from arsha.io. Inspect the output by eye: confirm item names look like real BDO ore/ingot names and values are plausible silver amounts (thousands, not fractions or absurd numbers). Then run:
```bash
python -m bdo_profit --category cooking --top 15 --csv cooking_results.csv
```
(no `--refresh-recipes` this time — confirms the cache load path works) and open `cooking_results.csv` to confirm it has real cooking recipe names and values.

- [ ] **Step 7: Commit**

```bash
git add bdo_profit/cli.py bdo_profit/__main__.py tests/test_cli.py
git commit -m "feat: wire up CLI with ranked profitability table and CSV export"
```

---

## After implementation

Report back to the user:
1. The three spec deviations listed under **Global Constraints** above (mastery-range midpoint instead of a dynamic breakpoint formula; dropped cooking quality tiers; bonus outputs detected from the same row instead of cross-referenced recipe IDs) — these change what "mastery-adjusted" means from the original spec and the user should know before trusting the numbers.
2. `data/npc_prices.json` is seeded empty (`{}`) — NPC-purchasable ingredients (Paprika, Strawberries, etc.) won't factor into cooking-cost comparisons until the user adds real item IDs and prices from in-game vendor tooltips.
3. `config/bonus_proc_rates.yaml` is seeded empty — all bonus/tier-skip outputs currently show as "upside" only, excluded from the core ranking, until the user observes real rates in-game and adds them.
4. A full `--refresh-recipes` run takes a few minutes (hundreds of individual cooking-recipe detail page fetches, deliberately rate-limited to be polite to bdocodex).
