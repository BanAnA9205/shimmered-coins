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
- [x] Extractor for real game data (reads `TerrariaServer.exe`), see [`extractor/README.md`](extractor/README.md). `shimcoins data fetch` downloads a server build and runs it.
- [x] Web UI (local, Terraria-style): `shimcoins serve`
- [ ] **Search engine: being rebuilt.** The first version searched move sequences step by step; it was removed in favour of the recipe-based design in [`docs/ALGORITHM.md`](docs/ALGORITHM.md). Phase 1 uses LPs over recipes with HiGHS; phase 2 orders each recipe and ranks by growth per action. Until it lands, `shimcoins search` and the UI's Search button report the size of the move graph instead of results.

# Quick start

```sh
pip install -e ".[dev]"
pytest

# get real game data: download the (free) dedicated server, extract, build the db
shimcoins data fetch           # picks a version, confirms, cleans up after itself
shimcoins data info data/1.4.5.8

# the bundled toy world (invented data) works everywhere a dataset does
shimcoins data info python/shimmered_coins/demo/toy_world.json
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

See [`docs/ALGORITHM.md`](docs/ALGORITHM.md). In short:

- **Phase 1:** moves form a sparse item × move table. The search finds the best *atomic
  recipes*: buy/craft/shimmer quantities that can't be split into smaller profitable pieces. It
  solves linear programs with HiGHS instead of walking move sequences.
- **Phase 2:** each top recipe is turned into the most compact ordered sequence: buy everything,
  run each craft or shimmer once at full quantity, sell all.
- **Ranking:** results are ranked by growth per action, `ln(1 + gain) / length`.

The options below describe the world and NPC setup. They already work and will feed the new engine:

| option | effect |
|--------|--------|
| `--npc Merchant=0.85@snow` | present NPC, its happiness price adjustment (0.75 happiest .. 1.5) and the biome(s) it lives in (`@any`: try every biome) |
| `--travel-stock "Item"` | what the Travelling Merchant sells today |
| `--all-npcs 1` | every shop NPC present at the given adjustment |
| `--flag hardmode` | world flags (progression, world evil, events, ...) |
| `--include`, `--exclude`, `--must-use`, `--start` | constrained search on specific items |
| `--kinds craft,shimmer` | allowed move kinds |
