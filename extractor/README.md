# TerrariaExtractor

This tool reads a Terraria **server** build and writes the dataset JSON described in
[`../docs/DATA_FORMAT.md`](../docs/DATA_FORMAT.md). It uses reflection only, so it builds without any
game files. Supported versions are 1.4.0 and later.

## 1. Get a `TerrariaServer.exe`

Pick one:

- **Automatically** (does everything below and cleans up): `shimcoins data fetch 1.4.5.8`.
  It downloads the free dedicated-server zip from terraria.org, builds and runs the extractor,
  writes `data/<version>/db.sqlite` and deletes the server files. `shimcoins data fetch` with no
  version shows a menu, `--list` shows what terraria.org has. 1.4.0.x was never published as zips;
  use the Steam beta branches for those.
- **Steam install:** `TerrariaServer.exe` sits next to `Terraria.exe`, for example
  `C:\Program Files (x86)\Steam\steamapps\common\Terraria\`.
  For older versions: right-click Terraria in Steam, open **Properties → Betas**, pick a legacy branch, then copy the folder.
- **Official dedicated-server zip** from terraria.org (the "PC Dedicated Server" link in the page footer).
  The zip has `Windows/`, `Linux/` and `Mac/` folders, and each one contains `TerrariaServer.exe`.

## 2. Build

| platform | command |
|----------|---------|
| Windows | `extractor\build.cmd` (uses the C# compiler bundled with .NET Framework 4) |
| Linux/macOS | `extractor/build.sh` (needs Mono: `apt install mono-mcs mono-runtime`) |

Both produce `bin/TerrariaExtractor.exe` and `bin/TerrariaExtractor32.exe`.

## 3. Run

```sh
# Windows (use TerrariaExtractor32.exe if you get a BadImageFormatException)
extractor\bin\TerrariaExtractor.exe "C:\path\to\Terraria\TerrariaServer.exe" --out dump-1.4.5.8.json

# Linux (point at the TerrariaServer.exe inside the server's Linux/ folder)
mono extractor/bin/TerrariaExtractor.exe ~/terraria-server/Linux/TerrariaServer.exe --out dump-1.4.5.8.json

# then
shimcoins data build dump-1.4.5.8.json        # -> data/1.4.5.8/db.sqlite
shimcoins data info data/1.4.5.8               # includes a check of the pricing formula
```

Use `--version 1.4.5.8` if the build reports its version oddly.

## When something breaks

The extractor logs warnings and keeps going section by section. If a section fails, run

```sh
TerrariaExtractor.exe <TerrariaServer.exe> --probe > probe.txt
```

and send `probe.txt`. It lists the members of the classes the extractor reads (Recipe, RecipeGroup,
ShimmerTransforms, NPCShopDatabase, Condition, ...), which is what's needed to adapt it to that build.

## How conditions are found

The extractor **probes**. It changes one piece of game state at a time and watches which items
appear, or whether a `Condition` holds. The state it changes:

- progression (`hardMode`, every `NPC.downed*`)
- time and moon phase
- events and seeds
- difficulty
- the player's biome zones
- which other town NPCs are present
- the NPC's happiness

This works the same way on 1.4.4+ `Condition` objects and on pre-1.4.4 hard-coded
`Chest.SetupShop` code. It produces typed conditions such as `zone:snow`, `!daytime`, `moon:2`,
`npc:22` and `happy<=0.9`. See [`../docs/DATA_FORMAT.md`](../docs/DATA_FORMAT.md) for the full
grammar. Anything no probe explains becomes a `player:<slug>` flag, for example a golf score or
bestiary completion.

- **Shimmer rules:** the extractor changes the progression flags and world evil, and records which
  flags change the result.
- **Pricing:** `pricing_probe` in the metadata stores samples of the game's `GetItemExpectedPrice`
  at different happiness levels, with and without the Discount Card flag. `shimcoins data info`
  compares them with the engine's formula (`buy_match_rate`, `discount_match_rate`).

`--legacy-shops` forces the pre-1.4.4 shop path, which is useful for debugging.

## Known gaps

- The Travelling Merchant's random stock isn't extracted. Enter today's stock with `--travel-stock` or in the UI.
- Pre-1.4.4 shops use an approximate shop-index → NPC table, and prices set inside `SetupShop` aren't captured.
- Probing finds conditions that depend on one or two pieces of state. `if/else` biome chains
  (e.g. "desert but not snow") come out as just `zone:desert`. That only matters if you put an NPC
  in two biomes at once.

The extracted data belongs to Re-Logic. Keep it local; `/data/` is gitignored.
