# TerrariaExtractor

This tool reads a Terraria **server** build and writes the dataset JSON described in
[`../docs/DATA_FORMAT.md`](../docs/DATA_FORMAT.md). It uses reflection only, so it builds without any
game files. Supported versions are 1.4.0 and later.

## 1. Get a `TerrariaServer.exe`

Pick one:

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

- **Shimmer rules and pre-1.4.4 shops:** the extractor sets `Main.hardMode`, every `NPC.downed*` flag and
  `WorldGen.crimson` to different values, calls the game's own functions each time, and records which
  flags change the result. Flag names are the game's field names in lowercase, for example
  `downedmoonlord` and `downedboss3`.
- **1.4.4+ shops:** entries carry `Condition` objects. These are named after the `Terraria.Condition`
  static field they match, lowercased, for example `hardmode` or `!bloodmoon`.
- **Pricing:** `pricing_probe` in the metadata stores samples of the game's `GetItemExpectedPrice` at
  different happiness levels. `shimcoins data info` compares them with the engine's formula.

## Known gaps

- The Travelling Merchant's random stock isn't extracted.
- Pre-1.4.4 shops use an approximate shop-index → NPC table, and prices set inside `SetupShop` aren't captured.
- Biome, moon-phase and time-of-day shop conditions only appear as `Condition` names (1.4.4+).
  Pass them as `--flag` when relevant.

The extracted data belongs to Re-Logic. Keep it local; `/data/` is gitignored.
