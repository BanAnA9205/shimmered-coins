# Project status

Written after the bug audit of 2026-09-30 (open PR #7, branch `feat/engine-sprites-world`).
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
- **NPC drop tables** — 444 NPCs / 2,368 EV entries on 1.4.5.8, extracted through the game's own
  `ReportDroprates` API. In the dataset (`npc_drops`) for the farming-EV engine.
- **`shimcoins serve`** — web UI with Search + Sprites tabs, the Sprites tab imports item art
  from the Steam install (LZX decoding, 6,134 sprites) with a preview grid.

## In flight

- **PR #7** (`feat/engine-sprites-world`): the recipe engine, sprites tab, world parser and drop
  tables, plus an audit pass (post-filter under-fetch fix, corrupt-world hardening, CLI fixes).
  Merge this before starting anything new; branch names are `feat/...` from now on.

## Local data assets (gitignored, machine-specific)

| asset | where |
|---|---|
| datasets | `data/1.4.5.8/db.sqlite`, `data/1.4.4.9/db.sqlite` |
| sprite art | `web/public/sprites/Item_*.png` (6,134) |
| world saves | `~/.local/share/Terraria/Worlds/*.wld` |
| mono toolchain | conda env `shimcoins-mono`; node for the web build in `shimcoins-node` |

If a dataset is missing: `shimcoins data fetch 1.4.5.8 --yes` (needs `shimcoins-mono` on PATH).
If sprites are missing: open the Sprites tab, or `shimcoins sprites`.

## Next up: the crafting-assistant pivot

The exploit-hunting days are done (1.4.5 patched the shop economy dry — the `data diff` between
1.4.4.9 and 1.4.5.8 shows the repricing). The roadmap shipped with the pivot discussion:

1. **Phase C — source engine** (`shimcoins plan`, the next milestone):
   - your items as bounded zero-cost columns (chests from `shimcoins world`),
   - farming as EV columns (the `npc_drops` data is ready and unused),
   - "what can I make / help me craft X" modes, effort in action units.
2. **Phase D — world integration in the UI**: world picker, storage-chest selection (the world
   format has no "who placed this chest" flag, so region-select), NPC detection with homes and
   happiness from the extracted preference database.
3. **Later**: MILP L-level Pareto front (§6.2 — `--max-places` is a post-filter, with the
   audit's over-fetch of 5× as a stopgap), neutral catalogue (§6.3), LZX-style deep checks for
   the 1.4.5.8 town-rooms section header.

## Conventions

- Branches: `feat/<thing>`; PRs into `main`; PRs get squashed after review.
- Tests: `pytest` (80 passing at audit time, incl. mono-backed extractor tests);
  lint `ruff check` + `ruff format --check`. The C# extractor builds with the mono env.
- Data never commits: game data, sprites and world saves stay local (`data/`, `web/public/`).
