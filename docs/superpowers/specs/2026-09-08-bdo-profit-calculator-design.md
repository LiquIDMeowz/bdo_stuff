# BDO Central Market Profitability Calculator — Design Spec

Date: 2026-09-08
Status: Approved for planning

## Purpose

A local CLI tool that tells the user which of their worker-empire-supplied raw
materials (ores, wood logs, cooking base materials like chicken meat/honey)
are most profitable to sell raw vs. process/cook — including comparing
multiple hops deep (e.g. Log → Plank → Square Timber), since a later tier is
not always worth more than an earlier one on the central market.

Also ranks cooking recipes, incorporating NPC-vendor-purchasable ingredients
(e.g. Paprika, Strawberries) as an alternative/cheaper input source alongside
market-bought ingredients.

Character context (from mastery screenshot, 2026-09-08): Gathering Artisan 5
(375), **Processing Artisan 7 (385)** with Chopping/Grinding/Shaking/Drying/
Filtering/Heating all at 930-940, **Cooking Master 7 (1040)**. Mastery affects
yield and is stored in an editable config, not hardcoded.

## Non-goals

- No hosting, no auth, no remote access — local script only, run on-demand at
  the user's PC (confirmed: PC-only access pattern, no phone/remote need).
- No live scraping of recipe data on every run — recipe/chain/mastery-table
  data is scraped once into a local cache and only re-scraped on demand
  (`--refresh-recipes`), since it only changes on game patches.
- No attempt to scrape garmoth.com (Cloudflare-protected, 403 on plain
  request — not worth bypass tooling for a hobby project).
- No Alchemy/Imperial crafting/trading in v1 — architecture should not
  preclude adding these later, but they are out of scope now.
- No guessing at unknown bonus-proc percentages — where the rate isn't known,
  it's marked `unknown` and excluded from the core ranking, not estimated.

## Data sources

| Data | Source | Method | Refresh cadence |
|---|---|---|---|
| Live market prices (current price, stock, last-sold price) | `api.arsha.io/v2/eu/GetWorldMarketSubList` | HTTP GET, rate-limited + retry/backoff client | Every run |
| Price history (liquidity/trend sanity check on shortlisted items only) | `api.arsha.io/v2/eu/GetMarketPriceInfo` | HTTP GET | Every run, shortlisted items only |
| Processing chains (Chopping/Heating/Grinding/Filtering/Drying/Shaking recipes, input:output ratios) | `bdocodex.com` | `requests` + `BeautifulSoup` (confirmed static HTML) | Cached to `data/recipes_cache.json`; re-scraped only on `--refresh-recipes` |
| Cooking recipes (ingredients, result dish(es), quality tiers) | `bdocodex.com` | same | same |
| Mastery yield/quality breakpoint tables (Processing extra-yield by mastery level, Cooking extra-proc & quality-tier odds by mastery level) | `bdocodex.com`, cross-checked against known reference values; flagged if a scrape looks anomalous | same | same |
| NPC vendor prices (Paprika, Strawberry, Sugar, Flour, Mineral Water, Cooking Wine, etc.) | Curated static file | Manually seeded, hand-edited | Manual |
| Bonus/tier-skip proc rates (extra material or extra material + next tier up) | Curated static file, `unknown` by default unless bdocodex publishes a rate | Manually seeded/edited by user after in-game observation | Manual |

**Region:** EU (`api.arsha.io/v2/eu/...`), confirmed as the user's BDO game
region.

**Rate limiting:** arsha.io proxies Pearl Abyss's official market API and
sits behind an Imperva WAF that throttles bursts (observed a `code:103`
block after a handful of rapid manual test requests). The client enforces
a minimum inter-request delay (~1 req/sec) and exponential backoff + retry
on `code:103` responses, plus a short in-run TTL cache so re-checking the
same item twice in one run doesn't double-hit the API.

## Data model — unified conversion graph

- `Item`: id, name, market sub-id, npc_price (optional, `None` if not
  NPC-purchasable).
- `ConversionEdge`: process type (Chopping/Heating/Grinding/Filtering/Drying/
  Shaking/Cooking), `inputs: [(Item, qty)]`, `base_outputs: [(Item, qty)]`
  (guaranteed, 100% — per user: "assume 100% that when processing/chopping
  we get the material we want"), `bonus_outputs: [(Item, qty, proc_rate)]`
  where `proc_rate` is a fraction or `unknown`.
- `bonus_outputs` entries are **additive**, not a replacement for base
  output, and are not restricted to the same tier as the base output — a
  Chopping edge on Planks can list Square Timber as a bonus output even
  though Square Timber is normally produced by a separate downstream edge.
  Per the user: a tier-skip proc grants the expected item **plus** the tier
  above it, not the tier above instead of the expected item.
- Raw materials the user's workers deliver (ore, logs, chicken meat, honey,
  etc.) are graph nodes with no inbound edge — the engine's starting points.
- The engine walks forward from each raw material through however many hops
  the scraped recipe data actually defines (no artificial depth cap),
  scoring market value at every node along the way, not just the leaves.

## Mastery-adjusted yield math

Mastery levels live in `config/mastery.yaml` (seeded from the 2026-09-08
screenshot values), user-editable when mastery changes.

- **Processing base yield:** `expected_qty = base_qty × (1 + extra-proc
  chance from the scraped Processing mastery breakpoint table for that
  sub-skill and the user's current mastery)`. This is part of the core
  (non-bonus) yield calc, since Processing's "extra item" mechanic is a
  known, scrapeable, mastery-driven rate — distinct from the *unknown*
  bonus/tier-skip procs described above.
- **Cooking base yield:** `expected_value = Σ over quality tiers (tier
  probability at the user's Cooking mastery × tier sell price) × (1 +
  extra-dish proc chance from Cooking mastery)`, minus ingredient costs
  (cheaper of market price or NPC price per ingredient) and any fixed CRON
  meal cost the recipe requires.
- **Bonus/tier-skip procs** (unknown-rate byproducts, e.g. Sweet vs. Sour
  Pickled Vegetables, or a Plank-chopping proc into Square Timber) are
  **not** included in the core expected-value number when their rate is
  `unknown`. They're computed as a separate "potential upside" annotation:
  `bonus_value = bonus_qty × best_path_value(bonus_item)` using the same
  recursive best-path value as if the item had been gathered normally, but
  only surfaced as an upside note, not folded into the ranking, until the
  user supplies an observed rate in `config/bonus_proc_rates.yaml`. Once a
  rate is supplied, that edge's core expected value includes it.

## Profitability engine

The engine evaluates items **bottom-up / leaves-first, memoized per item**,
since a node's value can depend on a downstream node's best-path value
(needed for valuing bonus/tier-skip procs once rates are known). For every
raw material node it computes:

- Sell-as-is value, net of market tax (default ~65% post-tax, configurable
  for Value Pack / family fame bonuses).
- Value at each downstream hop, net of that hop's input cost, using the
  mastery-adjusted core yield.
- The best path (which hop depth maximizes margin) with the full
  breakdown, so the user can see *why* — e.g. "10 logs → sell raw = X,
  process once → Y, process twice → Z, best = process once."
- Bonus/tier-skip upside shown alongside, separately, per the mastery math
  section above.

Output: a ranked table of raw materials with best action (sell raw /
process N hops), margin per raw unit, and (where action-time data is
available from bdocodex) margin per hour. If action-time data isn't
available, margin-per-hour is omitted rather than guessed.

## CLI & output

Local Python script, `rich`-formatted terminal table.

```
python -m bdo_profit --category all --top 20
python -m bdo_profit --refresh-recipes
python -m bdo_profit --csv out.csv
```

Flags: `--category {ore,wood,cooking,all}`, `--top N`, `--refresh-recipes`,
`--csv PATH`, `--mastery-file PATH` (default `config/mastery.yaml`).

## Project structure & stack

```
bdo_profit/
  market_client.py     # arsha.io wrapper: rate limit, retry/backoff, TTL cache
  scraper/
    bdocodex.py         # processing + cooking recipe & mastery-table scraping
  models.py             # Item, ConversionEdge, MasteryTable dataclasses
  profitability.py       # graph walk + mastery-adjusted margin engine (bottom-up, memoized)
  cli.py                # argparse entrypoint, rich table output
config/
  mastery.yaml
  bonus_proc_rates.yaml
data/
  recipes_cache.json
  npc_prices.json
tests/
```

Python 3.10+, type hints throughout. Dependencies: `requests`,
`beautifulsoup4`, `rich`, `pyyaml`. Pytest for `profitability.py` (fake
recipes/prices → assert correct ranking, margin, and bonus-upside math)
and for the market client's cache/retry logic (mocked HTTP, no live
network in tests). The scraper gets a light smoke test against a saved
HTML fixture rather than hitting the live site in CI.

## Error handling

- API blocked/down → warn and fall back to the last-cached price snapshot
  if available, clearly labeled stale with its timestamp.
- Recipe scrape fails or a recipe is missing fields → skip that item with a
  logged warning; never silently guess numbers.
- Unknown bonus-proc rate → excluded from core ranking, shown as upside
  only (see Mastery-adjusted yield math).

## Git / repo setup

Fresh repo at `/home/meow/BDO` (no git yet). Init git, commit directly to
master (solo hobby project, no collaborators), conventional commit
messages per the user's global CLAUDE.md.

## Decisions log (ADR-style)

- **ADR:** Use `api.arsha.io` as the sole market-price source (not
  `blackdesertmarket.com`) | Reason: confirmed working, documented, no
  auth, returns both current price and history | Tradeoffs: single point
  of failure if arsha.io goes down; acceptable for a hobby tool, revisit
  if it becomes unreliable.
- **ADR:** Scrape `bdocodex.com` only for recipe/mastery-table data, not
  `garmoth.com` or `bdolytics.com` | Reason: bdocodex serves static HTML
  and scraped cleanly in testing; garmoth returned a Cloudflare 403 bot
  challenge; bdolytics redirected unexpectedly and wasn't worth chasing
  when bdocodex already covers the need | Tradeoffs: single scrape
  source — if bdocodex is missing a specific recipe, the tool reports the
  gap rather than guessing.
- **ADR:** Recipe/mastery-table data is cached locally and only refreshed
  on explicit `--refresh-recipes`, not scraped every run | Reason: this
  data only changes on game patches; live market prices still fetch fresh
  every run | Tradeoffs: user must remember to refresh after a patch that
  changes recipes — acceptable since patches are infrequent and obvious.
- **ADR:** Bonus/tier-skip proc rates default to `unknown` and are excluded
  from the core ranked margin rather than estimated | Reason: user
  explicitly does not know the real rates and doesn't want a guessed
  number driving the ranking | Tradeoffs: core ranking may understate true
  profitability of chains with valuable bonus procs until the user
  calibrates `config/bonus_proc_rates.yaml` from observation.
- **ADR:** Local CLI script, no hosting | Reason: user only needs this at
  their gaming PC, no remote/phone access required | Tradeoffs: none
  meaningful for this use case.
