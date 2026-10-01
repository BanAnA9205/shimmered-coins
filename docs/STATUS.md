# Project status

Written after the bug audit of 2026-09-30, updated after the second audit of 2026-10-01 (PR #8,
branch `feat/engine-sprites-world`).
This file is the "where are we" summary; [`PLAN.md`](PLAN.md) is the original MVP plan and
[`ALGORITHM.md`](ALGORITHM.md) the engine design.

## What works right now

- **`shimcoins data fetch <version>`** — downloads a game server from terraria.org, runs the
  C# extractor, builds `data/<version>/db.sqlite`, deletes the server files. Two real datasets
  are built locally: **1.4.5.8** and **1.4.4.9** (1.4.4.9 is the comparison point: its shimmer
  economy was profitable before Re-Logic repriced it).
- **`shimcoins search <dataset> ...`** — the recipe engine (HiGHS LP over the item × move table,
  free-loop detection, Lawler top-K, tours with place counting, wallet scaling). Verified
  against the real 1.4.5.8 dataset; pricing formulas match the game's own probe 50/50.
- **`shimcoins world <file.wld>`** — parses real saves: 480 + 296 filled chests and town NPC
  homes across the two local worlds (`Feral_Dreamland_of_Pizza.wld`, `joe.wld`).
- **`shimcoins plan <dataset> --world <file.wld>`** — the pivot search: best plans out of
  your chest items, optional buying (`--wallet`) and farming EVs (`--farm`).
- **NPC drop tables** — 444 NPCs / 2,368 EV entries on 1.4.5.8, extracted through the game's own
  `ReportDroprates` API. In the dataset (`npc_drops`) for the farming-EV engine.
- **`shimcoins serve`** — web UI with Search + Sprites tabs, the Sprites tab imports item art
  from the Steam install (LZX decoding, 6,134 sprites) with a preview grid.

## In flight

- **PR #8** (`feat/engine-sprites-world`): the pivot plan engine (`shimcoins plan` + the UI's
  Plan tab), plus two audit passes. Branch names are `feat/...` from now on.
- Second audit fixed:
  - plans ignored how many items you hold and how much you can spend; now checked and scaled;
  - top-K could skip recipes whose moves contain another recipe's moves (ALGORITHM.md 3, 4.3);
  - `shimcoins plan` hung on the demo world: zero-profit cycles padded loss-making plans;
  - the Plan tab's unchecked items were ignored by the API;
  - `plan --json` crashed;
  - the "stopped early" flag never fired.

## Local data assets (gitignored, machine-specific)

| asset | where |
|---|---|
| datasets | `data/1.4.5.8/db.sqlite`, `data/1.4.4.9/db.sqlite` |
| sprite art | `web/public/sprites/Item_*.png` (6,134) |
| world saves | `~/.local/share/Terraria/Worlds/*.wld` |
| mono toolchain | conda env `shimcoins-mono`; node for the web build in `shimcoins-node` |

If a dataset is missing: `shimcoins data fetch 1.4.5.8 --yes` (needs `shimcoins-mono` on PATH).
If sprites are missing: open the Sprites tab, or `shimcoins sprites`.

## The pivot search (shipped 2026-10-01)

The crafting-assistant search is live: `shimcoins plan <dataset> --world your.wld`.

- **World stock:** every item lying in any chest of a parsed world becomes your stock; you
  uncheck what you'd rather keep (the save format does not record who placed a chest, so
  per-item choosing is the design, not a heuristic).
- **Sources:** held items (zero-cost columns bounded by how many you hold) + shops (bounded by
  `--wallet`) + farming (per-kill expected-value columns through the game's own loot tables),
  to be combined with crafts and shimmer moves as one profit-maximizing LP.
- **Farming checkbox:** turns the same run's farming columns on — plans that need drops are
  ranked alongside stock-only plans, amounts shown as expected values per kill.
- The UI has a **Plan** tab: pick a world, uncheck stock items, tick "with farming", run.
- CLI mirror: `shimcoins plan data/1.4.5.8 --world ~/.local/share/Terraria/Worlds/joe.wld
  --all-npcs 0.75 --wallet 50g --farm 500 --top 5`

## Still to do (later milestones)

1. **Goal mode:** "craft X the cheapest way from this stock" (backward reachability over the
   same graph) — the follow-up API/UI work in phase C.
2. **NPC homes & suggested happiness** from `shimcoins world` into the plan UI (the data is
   already parsed; only the wiring is left).
3. **Farming EV in the main path:** all farm recipes currently resolve to buy-only chains;
   a displayed constraint: bosses show up as droppers of one-per-kill treasures — verify
   these look right for summoned bosses vs. normal spawn-and-kill loops.
4. **MILP L-level Pareto front** (§6.2 — `--max-places` is a post-filter with 5× oversampling
   as a stopgap), neutral catalogue (§6.3).

## Conventions

- Branches: `feat/<thing>`; PRs into `main`; PRs get squashed after review.
- Tests: `pytest` (98 passing after the second audit, incl. mono-backed extractor tests);
  lint `ruff check` + `ruff format --check`. The C# extractor builds with the mono env.
- Data never commits: game data, sprites and world saves stay local (`data/`, `web/public/`).
