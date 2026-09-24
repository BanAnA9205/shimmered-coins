# shimmered-coins: MVP plan

## Context
The repo is empty apart from the README and LICENSE. The goal is an engine that searches for money-making sequences in Terraria, where
`gain = (coins_out − coins_in) / coins_in`.

A sequence is any interleaving of buy, craft, shimmer and sell moves. Examples: buy → shimmer → craft → sell, or buy → craft → shimmer → craft → shimmer → sell. A move can also use only part of the inventory. For example, you can decraft into a set of items, craft with a subset of them, and keep or sell the rest later.

Supported versions are **1.4.0 or later**. The current version is 1.4.5.8 (September 2026). It needs data for any game version, NPC and happiness inputs, both unconstrained and constrained search with a `max_steps` limit, and a Terraria-style local web UI.

Stack decisions:
- **Backend:** C++ search core, with Python for the data pipeline, API and orchestration. The user works in C++, Python and HPC libraries.
- **Frontend:** React + TypeScript + Vite, run as a local web app. The user hasn't picked a frontend stack yet, so this is my recommendation.
- **Data:** a small C# extractor that loads the game's own server binary.

---

## 1. Domain facts that shape the design (check each one against the extracted code)
- **Selling:** every shop NPC buys every sellable item for the same base amount, `value/5` in copper coins. Which NPC you sell to only matters through that NPC's **happiness price multiplier** (about 0.75–1.5). So the engine only needs one `sell_mult`, which is the best multiplier among available NPCs. It does not need a table of NPCs per item. *Check in `Player.GetItemExpectedPrice` / `ShopHelper` that happiness really changes sell prices and not only buy prices. If it doesn't, `sell_mult = 1`.*
- **Buying:** the price is `value × npc_happiness_mult × (0.8 if Discount Card/Greedy Ring)`. Shop stock depends on world state: hardmode, bosses defeated, moon phase, biome, world evil, day or night, and the Travelling Merchant's random stock. Some shops use special currencies (Defender Medals) and are out of scope for the MVP.
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
- `graph.npz` is compiled per *(version, world config)*. It stores transforms as CSR arrays (inputs, outputs, kind, cost) and is what the C++ core loads, which makes loading very fast.
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
**Node and move:**
- A node is a state: sparse inventory, coins spent, coins held, and the steps used.
- A move is a *(transform, quantity)* pair.
- The tree matches your model: the root is an empty inventory, each child applies one move, and a leaf is scored after optional liquidation.

**Moves can come in any order, and quantities are worked out separately from the choice of moves.**
- **Tree levels:** the search tree branches on *which* transform to apply next. Any kind can follow any kind, and repeats are allowed (buy → shimmer → craft → shimmer → craft → sell …). The only limits are `max_steps` and the constraints.
- **How much each move uses:** decided per branch by a small **linear program**, not by an "all-in" rule. Each move uses a quantity `x_i ≥ 0`, and the inventory after every step must stay ≥ 0. Anything not consumed stays in the inventory: part of a decraft's output can be crafted later, part sold, and part left over.
- **Capital:** the "initial cost" is the peak coin outlay, meaning the largest cumulative spend minus revenue over all the steps so far. Buying later with money from earlier sales doesn't add to it.
- **Solving the ratio objective:** normalize capital to ≤ 1 (the Charnes–Cooper transform) and maximize final net coins. This is an ordinary LP with only a few variables, and the optimal value *is* the gain. Parent solutions warm-start the child LPs.
- **Whole numbers:** a *quantizer* turns the fractional solution into whole items and whole batches, respecting batch sizes for crafting and decrafting and rounding down to whole copper. It scales by the smallest workable integer multiplier, or uses a tiny MILP when that isn't exact.

**Algorithms**, all sharing one interface:
1. **Exhaustive DFS** up to `max_steps`, for small depths and as the correctness reference.
2. **Branch-and-bound best-first / beam search.** It uses a precomputed upper bound `V_k(item)`, the best liquidation value reachable within k steps. This is dynamic programming over the graph where craft edges treat co-ingredients as free, so the bound never underestimates. A node is pruned when `coins + Σ V_{remaining}(inv) ≤ best`. The search also keeps a transposition table on canonical inventory.
3. **Loop detection:** a sequence that goes coins → coins with gain > 0 using only unlimited-stock purchases is reported as a *repeatable loop*.

**Parameters:**
- `max_steps`, `max_budget`, `beam_width`, `top_n`, `time_limit`.
- `allowed_move_kinds`, `include_items`, `exclude_items`, `must_use_items`, `target_items` (constrained mode).
- `leftover_policy` (discard / value at sell price / must liquidate), `min_gain`.

**Output:** the top-N sequences, each with its moves and exact quantities, coins in and out, gain, and a repeatable-loop flag.

**Implementation order:**
1. A pure-Python reference engine (`python/shimmered_coins/search/`), used for tests and parity checks.
2. A C++ core (`cpp/`) with the same algorithms, parallelized with OpenMP by splitting the root and sharing the incumbent best score atomically. It's exposed to Python through **nanobind** and built with scikit-build-core and CMake.
3. Optional **MILP cross-check** using HiGHS (`highspy`) for unlimited mode. Edges become integer activity counts with flow conservation and binary "edge used" variables, `Σ used ≤ max_steps`, and the objective is to maximize net coins. This gives the global optimum as a check on the tree search, and the order of moves is recovered by topological sort.

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
cpp/{include,src,bindings}   C++20 core + nanobind module
web/                  React+TS+Vite
tests/{python,cpp}    pytest + Catch2; fixtures = tiny hand-made graphs
data/                 gitignored per-version DBs
pyproject.toml, CMakeLists.txt, .github/workflows/ci.yml
```

## 7. Milestones

**Status (2026-09-24):** milestones 1 and 3–5 are done. Milestone 2 is done against a mock game assembly and is waiting on a real `TerrariaServer.exe` run.

Differences from the original plan:
- The search is a DFS branch-and-bound, not beam search.
- Negative-gain reporting (`allow_loss`) is Python-only.
1. Scaffolding: pyproject, CMake, CI (pytest, ruff, clang-format), and a toy-graph fixture.
2. Extractor and DB build for 1.4.5.8 (current) and 1.4.4.9, plus `data diff`. After that, add the 1.4.0–1.4.3 adapter.
3. Model and pricing, with the Python reference search and CLI (`shimcoins search --max-steps 4 ...`).
4. C++ core with parity tests against Python, plus benchmarks.
5. FastAPI and web UI MVP.
6. Extras: MILP cross-check, sprite import, happiness calculator.

## 8. Verification
- Unit tests on interleaved sequences. One example: decraft into A+B+C, craft with A only, then sell B and C. The test checks that the LP uses part of the inventory correctly and computes peak capital correctly.
- Unit tests on hand-made graphs with known optimal loops: decraft batch remainders, rounding down to whole copper, happiness multipliers, and the rule that pruning never drops the true optimum (DFS vs B&B).
- Data sanity: spot-check item values and recipes against the wiki, and check that counts per version look reasonable.
- **Version regression:** reproduce the loops shown in the inspiration video on the early 1.4.4.x data, and confirm they disappear in later patches.
- Parity: C++ and Python give identical top-N results on random graphs.
- UI: run it locally, run a search end to end, and check the screenshot.

*Environment note:* the terraria.org and wiki domains are blocked from this cloud sandbox. Extraction has to run on your machine, or those domains need to be added to the environment's network allowlist. Everything else can be built and tested here with the fixture data.
