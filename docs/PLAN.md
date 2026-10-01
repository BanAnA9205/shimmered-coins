# shimmered-coins: MVP plan

## Context
The repo is empty apart from the README and LICENSE. The goal is an engine that searches for money-making sequences in Terraria, where
`gain = (coins_out − coins_in) / coins_in`.

A chain always has the shape **buys → any mix of crafts and shimmers → sell everything**. Crafts and shimmers can interleave freely, and a move can use only part of what you hold: decraft into a set of items, craft with a subset, and keep or sell the rest. The search itself is designed in [`ALGORITHM.md`](ALGORITHM.md).

Supported versions are **1.4.0 or later**. The current version is 1.4.5.8 (September 2026). It needs data for any game version, NPC and happiness inputs, both unconstrained and constrained search with a `max_steps` limit, and a Terraria-style local web UI.

Stack decisions:
- **Backend:** Python, with established HPC libraries doing the heavy lifting: HiGHS (`highspy`) for LP/MILP, and `scipy.sparse`/NumPy for the table. There is no hand-written solver. The earlier C++ core was removed once the design moved to LPs.
- **Frontend:** React + TypeScript + Vite, run as a local web app. The user hasn't picked a frontend stack yet, so this is my recommendation.
- **Data:** a small C# extractor that loads the game's own server binary.

---

## 1. Domain facts that shape the design (check each one against the extracted code)
- **Selling:** every shop NPC buys every sellable item for the same base amount, `value/5` in copper coins. Which NPC you sell to only matters through that NPC's **happiness price multiplier** (about 0.75–1.5). So the engine only needs one `sell_mult`, which is the best multiplier among available NPCs. It does not need a table of NPCs per item. *Check in `Player.GetItemExpectedPrice` / `ShopHelper` that happiness really changes sell prices and not only buy prices. If it doesn't, `sell_mult = 1`.*
- **Buying:** the price is `value × npc_happiness_mult × (0.8 if Discount Card/Greedy Ring)`. Shop stock depends on world state: hardmode, bosses defeated, moon phase, biome, world evil, and day or night. The Travelling Merchant (random stock) is not supported in this version. Some shops use special currencies (Defender Medals) and are out of scope for the MVP.
- **Shimmer quirks to model:**
  - One-to-one transmutations (`ItemID.Sets.ShimmerTransformToItem`), some locked behind progression.
  - **Decrafting** uses the *first* decraftable recipe and consumes full batches only (needs `stack ≥ recipe.createStack`; the remainder is left over).
  - Recipe groups ("any wood") give back the recipe's listed concrete item.
  - Some recipes are flagged not decraftable, and some results depend on world evil or progression.
  - Coins thrown in are consumed (they only grant luck), so treat them as a sink.
  - A whole stack is processed at once.
  - Shimmer only exists from 1.4.4 on. On 1.4.0–1.4.3 the engine runs without shimmer moves, as plain buy/craft/sell arbitrage. NPC happiness exists from 1.4.0, so it's available on every supported version.
- **1.4.5.x:** the game's internals probably changed. The extractor gets one adapter per code era: 1.4.0–1.4.3, 1.4.4 and 1.4.5. Each adapter is checked against its own version's data.
- **Crafting:** stations are world conditions and are assumed to be available. The Alchemy Table's 1/3 chance to save ingredients is off by default and can be turned on as an expected-value option.

## 2. Data acquisition (version-flexible)
**Source:** the official dedicated-server binary `TerrariaServer.exe`. It is headless and contains the same Item, Recipe, Shop and Shimmer code as the game. There are three ways to get it:
1. The terraria.org download link `https://terraria.org/api/download/pc-dedicated-server/terraria-server-XXXX.zip`. It sits in the site footer, not in the news section, and is published per patch (for example `1449`). Some old patches may be missing.
2. **Your Steam install.** `TerrariaServer.exe` ships next to `Terraria.exe`, and Steam's *Betas* tab offers legacy branches (for example 1.4.3.6 and 1.4.0.5). This covers versions that have no zip.
3. DepotDownloader with a specific manifest ID, for exact patches (needs ownership).

**Extractor** (`extractor/`, a C# console app targeting net48, run with Mono on Linux): it loads the assembly, initializes the static content, and dumps the data.
- Items: loop over `ItemID.Count` calling `SetDefaults`. Record value, maxStack, name and flags.
- Recipes: `Recipe.SetupRecipes()` then read `Main.recipe[]`, plus recipe groups, conditions and the not-decraftable flags.
- Shimmer: the transmute sets and the decraft rules.
- Shops:
  - 1.4.4+: read `NPCShopDatabase`, whose entries carry `Condition` objects that the extractor serializes.
  - Earlier versions: call `Chest.SetupShop` once for each combination of world flags and record what appears.
- Output: versioned JSON dumps, which Python ingests.
- Fallback for when the extractor can't run: a wiki Cargo exporter for the latest version only, also used to cross-check.

**Storage:** `data/<version>/` is gitignored. Extracted game data stays local and is never committed.
- `db.sqlite` is the canonical, curatable store. It has these tables:
  - `items`, `recipes`, `recipe_ingredients`, `recipe_groups`
  - `shops(npc, item, price_override, currency, condition)`
  - `shimmer_transmute`, `shimmer_decraft`, `npcs`
  - `meta(version, source_sha256, extracted_at)`
- `overrides.yaml` holds manual curation patches, applied at build time.
- The engine compiles the move graph for a *(version, world config)* into a sparse item × move table (`scipy.sparse`), which HiGHS solves directly.
- Commands: `shimcoins data fetch|extract|build|diff <v1> <v2>`. The diff shows what changed between patches, which is useful for seeing when a loop was fixed.

## 3. Model
- **WorldConfig:** version, hardmode, bosses defeated, world evil, moon phase, day or night, and whether shimmer is available.
- **NpcConfig:** a list of `{npc, happiness_mult}` and `discount_card`. In the MVP you enter the multiplier directly, with presets. A calculator that works out happiness from biome and neighbours comes later.
- **Transform graph:** every move is a linear edge `inputs → outputs` on a vector of items plus coins:
  - `BUY(npc, item)`: coins → 1 item.
  - `SELL(item)`: 1 item → coins, using the per-unit price rounded down to whole copper.
  - `CRAFT(recipe)`: ingredients → `createStack` of the result.
  - `SHIMMER_T(item)`: 1 → 1.
  - `SHIMMER_D(item)`: `createStack` → ingredients.

  Edges are filtered by the world config and by the search constraints.

## 4. Search engine

**Superseded.** The first version searched move sequences step by step, with a branch-and-bound tree, a per-sequence LP and a C++ port. It was replaced by the recipe-based design in [`ALGORITHM.md`](ALGORITHM.md):

- **Phase 1:** ranks atomic recipes over the sparse item × move table with HiGHS. It checks for free-money loops first, then finds the best recipe and uses Lawler ranking for the top K.
- **Phase 2:** orders each recipe into the most compact sequence and ranks by growth per trip (places visited; see ALGORITHM.md 5-6).

The old engine code was deleted. `search/quantize.py` (integer plans and inventory replay) is kept for phase 2.

## 5. API and UI
- **FastAPI** (`python/shimmered_coins/api/`) with endpoints:
  - `GET /versions`, `GET /items?q=`, `GET /npcs`.
  - `POST /search`, which returns a job ID and streams progress over SSE or WebSocket.
  - `GET /search/{id}`.
- **Web** (`web/`, React + TS + Vite):
  - **Styling:** custom Terraria-style CSS, meaning blue translucent inventory-slot panels, 9-slice `border-image` frames and pixel-perfect scaling.
  - **UI kit:** starts from **RPGUI** (zlib licence) or **NES.css** (MIT) for components.
  - **Font:** the font slot is pluggable. The repo ships a free OFL pixel font as the default. `web/public/fonts/` is gitignored, so you can drop in the Terraria font from dafontstyle, or Andy from your own game files, and the UI picks it up automatically. If you later confirm that font's licence allows redistributing it with a free app, it can be bundled by default. Item sprites follow the same rule: they're loaded from local game files and never committed.
  - **Screens:**
    - World and version setup.
    - NPC roster, with a happiness slider per NPC.
    - Search parameters.
    - Results list, where each chain shows item slots → arrows labelled with the move type → coins.
    - A tree explorer for the expanded nodes.
  - **Item sprites:** loaded from your local game `Content/Images` (XNB decoding) into a local cache. They are never committed.

## 6. Repo layout
```
extractor/            C# net48 console (TerrariaExtractor)
python/shimmered_coins/{data,model,search,api,cli.py}
web/                  React+TS+Vite
tests/                pytest; fixtures = tiny hand-made worlds
data/                 gitignored per-version DBs
pyproject.toml, .github/workflows/ci.yml
```

## 7. Milestones

**Status (2026-09-25):**
- Milestones 1, 2 and 4 are done.
  - Milestone 2 is done: `shimcoins data fetch` downloads a real server zip from terraria.org, runs the extractor and cleans up. It has been run for real against 1.4.5.8 (`data/1.4.5.8/db.sqlite`: 6195 items, 3603 recipes, 619 shop entries, 3347 shimmer rules; the pricing probe matches the engine's price formulas 50/50).
- Milestone 3 is being redone: the step-by-step engine was removed in favour of [`ALGORITHM.md`](ALGORITHM.md).

1. Scaffolding: pyproject, CI (pytest, ruff), a toy-world fixture.
2. Extractor and DB build for 1.4.5.8 (current) and 1.4.4.9, plus `data diff`. After that, add the 1.4.0–1.4.3 adapter.
3. Search engine, per [`ALGORITHM.md`](ALGORITHM.md) section 10:
   - phase 1 on HiGHS (free-loop check, best recipe, Lawler top-K);
   - phase 2 ordering and the length model;
   - growth-per-action ranking;
   - `shimcoins search` and the API/UI brought back on it.
4. FastAPI and web UI MVP.
5. Extras: sprite import, happiness calculator.

## 8. Verification
- The engine tests are listed in [`ALGORITHM.md`](ALGORITHM.md) section 10.
- Data sanity: spot-check item values and recipes against the wiki, and check that counts per version look reasonable.
- **Version regression:** reproduce the loops shown in the inspiration video on the early 1.4.4.x data, and confirm they disappear in later patches.
- UI: run it locally, run a search end to end, and check the screenshot.

*Environment note:* the terraria.org and wiki domains are blocked from this cloud sandbox. Extraction has to run on your machine, or those domains need to be added to the environment's network allowlist. Everything else can be built and tested here with the fixture data.
