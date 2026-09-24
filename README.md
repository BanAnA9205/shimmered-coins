# shimmered-coins
an engine for searching (possibly constrained) optimal coin-producing loops in terraria using shimmer mechanic 

# Inspiration
https://www.youtube.com/watch?v=Bp3ih9SbIGE

# Purpose
With the introduction of the Shimmering mechanic in Terraria, many material loops have been found (and subsequently fixed in later updates).

# Status

Early MVP. The roadmap is in [`docs/PLAN.md`](docs/PLAN.md).

- [x] Dataset schema, SQLite storage, curation overrides, version diff ([`docs/DATA_FORMAT.md`](docs/DATA_FORMAT.md))
- [x] Move graph: buy / sell / craft / shimmer, with NPC happiness pricing and world conditions
- [x] Reference search engine (Python): tree over move sequences, per-sequence LP for quantities, branch-and-bound, exact integer plans
- [x] Extractor for real game data (reads `TerrariaServer.exe`), see [`extractor/README.md`](extractor/README.md). Tested against a mock game assembly; waiting on a real build.
- [x] C++ search core (`pip install ./cpp`): same algorithm with a dedicated simplex solver and OpenMP, used automatically when installed, parity-tested against the Python engine
- [x] Web UI (local, Terraria-style): `shimcoins serve`

# Quick start

```sh
pip install -e ".[dev]"
pip install ./cpp          # optional C++ core, ~1000x faster per search node
pytest

# get real game data: download the (free) dedicated server, extract, build the db
shimcoins data fetch           # picks a version, confirms, cleans up after itself
shimcoins search data/1.4.5.8 --all-npcs 1 --flag hardmode --max-steps 5

# or search the bundled toy world (invented data)
shimcoins search python/shimmered_coins/demo/toy_world.json --all-npcs 1 --flag downedBoss --max-steps 5
```

## Web UI

```sh
pip install -e ".[api]"
(cd web && npm install && npm run build)
shimcoins serve            # http://127.0.0.1:8765, datasets from ./data (plus the demo world)
```

For UI development, run `npm run dev` inside `web/` while `shimcoins serve` is running. The dev server proxies `/api` to it.

- **Font:** put a Terraria-style font at `web/public/fonts/terraria.ttf`. It's gitignored and used automatically.
- **Item sprites:** `web/public/sprites/Item_<id>.png`, also gitignored.

## How the search works

- **Moves:** a sequence is any interleaving of *buy*, *craft*, *shimmer* and *sell* moves, up to `--max-steps`. The search tree only decides **which** move comes next.
- **Quantities:** for each sequence, a small linear program decides **how many** batches each move uses. A move can use part of what you hold. For example, you can decraft into A+B+C, craft with A, and sell B later.
- **Capital:** capital is the peak coin outlay over the sequence, and `gain = profit / capital`.
- **Whole numbers:** the continuous solution is scaled to the smallest plan with whole items and batches, with prices rounded down to whole copper.
- **Pruning:** optimistic "best value reachable in k moves" bounds cut subtrees that can't beat the current top-N. `--exhaustive` turns pruning off, which is useful for checking results.

Useful options:

| option | effect |
|--------|--------|
| `--npc Merchant=0.85@snow` | present NPC, its happiness price adjustment (0.75 happiest .. 1.5) and the biome(s) it lives in (`@any`: try every biome) |
| `--travel-stock "Item"` | what the Travelling Merchant sells today |
| `--all-npcs 1` | every shop NPC present at the given adjustment |
| `--flag hardmode` | world flags (progression, world evil, events, ...) |
| `--include`, `--exclude`, `--must-use`, `--start` | constrained search on specific items |
| `--kinds buy,craft,sell` | allowed move kinds |
| `--leftover sell` | count leftover items at sell value instead of discarding them |
| `--allow-loss` | also report the least-bad sequences when nothing is profitable |
| `--budget "5p"` | scale the plan to a coin budget |
| `--json` | machine-readable output |
