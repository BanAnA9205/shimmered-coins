import { useEffect, useMemo, useState } from "react";
import {
  api,
  type DatasetInfo,
  type DatasetSummary,
  type ItemRef,
  type Kind,
  type NpcIn,
  type SearchResponse,
} from "./api";
import { ItemPicker } from "./components/ItemPicker";
import { ResultCard } from "./components/ResultCard";
import { PlanTab } from "./components/PlanTab";
import { SpritesTab } from "./components/SpritesTab";

const ALL_KINDS: Kind[] = ["buy", "craft", "shimmer"];

interface Settings {
  dataset: string;
  flags: string[];
  shimmer: boolean;
  discount: boolean;
  npcs: Record<number, NpcIn>; // present NPC -> happiness price adjustment + biome
  kinds: Kind[];
  topN: number;
  overhead: number; // fixed per-loop effort c in the score
  maxPlaces: number; // 0 = any
  wallet: string; // e.g. "50g", "1p 20g", "500" (copper); "" = unlimited
  allowLoss: boolean;
  timeLimit: number;
  include: ItemRef[];
  exclude: ItemRef[];
  mustUse: ItemRef[];
  start: ItemRef[];
}

const DEFAULTS: Settings = {
  dataset: "demo",
  flags: [],
  shimmer: true,
  discount: false,
  npcs: {},
  kinds: ALL_KINDS,
  topN: 10,
  overhead: 1,
  maxPlaces: 0,
  wallet: "",
  allowLoss: false,
  timeLimit: 30,
  include: [],
  exclude: [],
  mustUse: [],
  start: [],
};

const GROUP_LABEL: Record<string, string> = {
  progression: "Progression",
  time: "Time & moon",
  events: "Events",
  seeds: "Seeds & difficulty",
  player: "Player",
  other: "Other",
};

const NEUTRAL: NpcIn = { adjustment: 1, zones: [] };

const STORAGE_KEY = "shimmered-coins.settings.v2";

function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const saved = { ...DEFAULTS, ...JSON.parse(raw) } as Settings;
      // settings saved before NPC biomes existed stored a bare adjustment per NPC
      for (const [id, v] of Object.entries(saved.npcs as Record<string, unknown>)) {
        if (typeof v === "number") saved.npcs[+id] = { adjustment: v, zones: [] };
      }
      return saved;
    }
  } catch {
    /* storage unavailable */
  }
  return DEFAULTS;
}

/** "50g", "1p 20g", "500" -> copper; null when empty or unparsable. */
function parseCoins(text: string): number | null {
  const t = text.trim().toLowerCase();
  if (!t) return null;
  if (/^\d+$/.test(t)) return +t;
  const units: Record<string, number> = { p: 1_000_000, g: 10_000, s: 100, c: 1 };
  let copper = 0;
  let hit = false;
  for (const m of t.matchAll(/(\d+)\s*([pgsc])/g)) {
    copper += +m[1] * units[m[2]];
    hit = true;
  }
  return hit ? copper : null;
}

/** Terraria's happiness text for a given shop price adjustment (approximate bands). */
function mood(adj: number): string {
  if (adj <= 0.8) return "Loves it here";
  if (adj <= 0.92) return "Happy";
  if (adj <= 1.04) return "Content";
  if (adj <= 1.2) return "Unhappy";
  return "Hates it here";
}

export default function App() {
  const [tab, setTab] = useState<"search" | "plan" | "sprites">("search");
  const [s, setS] = useState<Settings>(loadSettings);
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [info, setInfo] = useState<DatasetInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resp, setResp] = useState<SearchResponse | null>(null);

  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS((o) => ({ ...o, [k]: v }));

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
    } catch {
      /* ignore */
    }
  }, [s]);

  useEffect(() => {
    api.datasets().then(setDatasets, (e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    setInfo(null);
    api.dataset(s.dataset).then(
      (d) => {
        setInfo(d);
        setS((o) => {
          // First visit to this dataset: every shop NPC present and content.
          const known = d.npcs.filter((n) => n.id in o.npcs);
          const npcs = known.length
            ? Object.fromEntries(known.map((n) => [n.id, o.npcs[n.id]]))
            : Object.fromEntries(d.npcs.map((n) => [n.id, NEUTRAL]));
          const known_flags = Object.values(d.flag_groups).flatMap((g) => g.map((f) => f.name));
          return { ...o, npcs, flags: o.flags.filter((f) => known_flags.includes(f)) };
        });
      },
      (e) => setError(String(e.message ?? e)),
    );
  }, [s.dataset]);

  const presentCount = useMemo(() => Object.keys(s.npcs).length, [s.npcs]);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.search({
        dataset: s.dataset,
        flags: s.flags,
        shimmer: s.shimmer,
        discount: s.discount,
        npcs: s.npcs,
        kinds: s.kinds,
        include: s.include.length ? s.include.map((i) => i.id) : null,
        exclude: s.exclude.map((i) => i.id),
        must_use: s.mustUse.map((i) => i.id),
        start: s.start.length ? s.start.map((i) => i.id) : null,
        top_n: s.topN,
        overhead: s.overhead,
        max_places: s.maxPlaces > 0 ? s.maxPlaces : null,
        wallet: parseCoins(s.wallet),
        allow_loss: s.allowLoss,
        time_limit: s.timeLimit,
      });
      setResp(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = <T,>(list: T[], v: T) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  return (
    <div className="app">
      <header className="title">
        <h1>Shimmered Coins</h1>
        <p className="subtitle">find the most profitable buy · craft · shimmer · sell chains</p>
        <nav className="tab-bar">
          <button
            className={`tab ${tab === "search" ? "active" : ""}`}
            onClick={() => setTab("search")}
          >
            Search
          </button>
          <button
            className={`tab ${tab === "plan" ? "active" : ""}`}
            onClick={() => setTab("plan")}
          >
            Plan
          </button>
          <button
            className={`tab ${tab === "sprites" ? "active" : ""}`}
            onClick={() => setTab("sprites")}
          >
            Sprites
          </button>
        </nav>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <section className="panel">
            <h2>World</h2>
            <label className="field-label">Dataset</label>
            <select
              className="t-input"
              value={s.dataset}
              onChange={(e) => set("dataset", e.target.value)}
            >
              {datasets.map((d) => (
                <option key={d.id} value={d.id} disabled={!!d.error}>
                  {d.id}
                  {d.version && d.version !== d.id ? ` (${d.version})` : ""}
                  {d.error ? " – broken" : ""}
                </option>
              ))}
            </select>
            {info && (
              <p className="muted">
                {info.items} items · {info.recipes} recipes · {info.shimmer_rules} shimmer rules
              </p>
            )}
            {info &&
              Object.entries(info.flag_groups).map(([group, flags]) => (
                <div key={group}>
                  <label className="field-label">{GROUP_LABEL[group] ?? group}</label>
                  <div className="flag-grid">
                    {flags.map((f) => (
                      <label key={f.name} className="check" title={f.name}>
                        <input
                          type="checkbox"
                          checked={s.flags.includes(f.name)}
                          onChange={() => set("flags", toggle(s.flags, f.name))}
                        />
                        <span>{f.description}</span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            <label className="check">
              <input
                type="checkbox"
                checked={s.shimmer}
                onChange={(e) => set("shimmer", e.target.checked)}
              />
              <span>Shimmer available</span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={s.discount}
                onChange={(e) => set("discount", e.target.checked)}
              />
              <span>Discount Card</span>
            </label>
          </section>

          <section className="panel">
            <h2>
              Town NPCs <span className="muted">({presentCount})</span>
            </h2>
            <p className="muted">
              Any shop NPC buys anything; only the happiest one's price matters when selling.
            </p>
            <div className="npc-grid">
              {info?.npcs.map((n) => {
                const present = n.id in s.npcs;
                const npc = s.npcs[n.id] ?? NEUTRAL;
                const update = (patch: Partial<NpcIn>) =>
                  set("npcs", { ...s.npcs, [n.id]: { ...npc, ...patch } });
                const zoneValue = npc.zones === "any" ? "any" : (npc.zones[0] ?? "");
                const setAdj = (raw: string) => {
                  const v = +raw;
                  update({ adjustment: Number.isFinite(v) ? Math.min(1.5, Math.max(0.75, v)) : 1 });
                };
                return (
                  <div key={n.id} className={`npc ${present ? "" : "absent"}`}>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={present}
                        onChange={() => {
                          const next = { ...s.npcs };
                          if (present) delete next[n.id];
                          else next[n.id] = NEUTRAL;
                          set("npcs", next);
                        }}
                      />
                      <span className="npc-name">{n.name}</span>
                    </label>
                    {present && (
                      <div className="npc-happy">
                        <div className="npc-adj-row">
                          <input
                            type="range"
                            min={0.75}
                            max={1.5}
                            step={0.01}
                            value={npc.adjustment}
                            onChange={(e) => setAdj(e.target.value)}
                          />
                          <input
                            className="t-input npc-adj-input"
                            type="number"
                            min={0.75}
                            max={1.5}
                            step={0.01}
                            value={npc.adjustment}
                            title="shop price adjustment (0.75 happiest .. 1.5)"
                            onChange={(e) => setAdj(e.target.value)}
                          />
                        </div>
                        <span className="npc-adj" title="shop price adjustment">
                          {mood(npc.adjustment)}
                        </span>
                        {info.zones.length > 0 && (
                          <select
                            className="t-input npc-zone"
                            title="where the NPC lives: biome-specific stock depends on it"
                            value={zoneValue}
                            onChange={(e) => {
                              const v = e.target.value;
                              update({ zones: v === "any" ? "any" : v ? [v] : [] });
                            }}
                          >
                            <option value="">no biome</option>
                            {info.zones.map((z) => (
                              <option key={z} value={z}>
                                {z}
                              </option>
                            ))}
                            <option value="any">anywhere</option>
                          </select>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        </aside>

        <main className="main">
          {tab === "plan" ? (
            <PlanTab
              dataset={s.dataset}
              flags={s.flags}
              shimmer={s.shimmer}
              discount={s.discount}
              npcs={s.npcs}
            />
          ) : tab === "sprites" ? (
            <SpritesTab dataset={s.dataset} />
          ) : (
          <>
          <section className="panel search-panel">
            <h2>Search</h2>
            <div className="row">
              <div className="field">
                <label className="field-label">Show top</label>
                <input
                  className="t-input small"
                  type="number"
                  min={1}
                  max={100}
                  value={s.topN}
                  onChange={(e) => set("topN", Math.max(1, +e.target.value))}
                />
              </div>
              <div className="field">
                <label className="field-label">Loop overhead (c)</label>
                <input
                  className="t-input small"
                  type="number"
                  min={0}
                  max={10}
                  step={0.5}
                  value={s.overhead}
                  onChange={(e) => set("overhead", Math.max(0, +e.target.value))}
                />
              </div>
              <div className="field">
                <label className="field-label">Max places</label>
                <input
                  className="t-input small"
                  type="number"
                  min={0}
                  max={20}
                  value={s.maxPlaces}
                  onChange={(e) => set("maxPlaces", Math.max(0, +e.target.value))}
                />
              </div>
              <div className="field">
                <label className="field-label">Time limit (s)</label>
                <input
                  className="t-input small"
                  type="number"
                  min={1}
                  max={300}
                  value={s.timeLimit}
                  onChange={(e) => set("timeLimit", Math.max(1, +e.target.value))}
                />
              </div>
            </div>
            <div className="row">
              <div className="field">
                <label className="field-label">Wallet</label>
                <input
                  className="t-input small"
                  type="text"
                  placeholder="e.g. 50g"
                  value={s.wallet}
                  title="drop recipes you can't afford, scale the rest to your wallet"
                  onChange={(e) => set("wallet", e.target.value)}
                />
              </div>
            </div>
            <div className="row">
              <label className="field-label">Moves</label>
              {ALL_KINDS.map((k) => (
                <label key={k} className="check">
                  <input
                    type="checkbox"
                    checked={s.kinds.includes(k)}
                    onChange={() => set("kinds", toggle(s.kinds, k))}
                  />
                  <span className={`badge badge-${k}`}>{k}</span>
                </label>
              ))}
              <label className="check">
                <input
                  type="checkbox"
                  checked={s.allowLoss}
                  onChange={(e) => set("allowLoss", e.target.checked)}
                />
                <span>show losing chains too</span>
              </label>
            </div>
            <details className="constraints">
              <summary>Focus on specific items</summary>
              <div className="pickers">
                <ItemPicker
                  dataset={s.dataset}
                  label="Must involve"
                  hint="every chain must touch all of these"
                  value={s.mustUse}
                  onChange={(v) => set("mustUse", v)}
                />
                <ItemPicker
                  dataset={s.dataset}
                  label="Start by buying"
                  hint="the first purchase must be one of these"
                  value={s.start}
                  onChange={(v) => set("start", v)}
                />
                <ItemPicker
                  dataset={s.dataset}
                  label="Only use"
                  hint="whitelist: chains may only touch these items"
                  value={s.include}
                  onChange={(v) => set("include", v)}
                />
                <ItemPicker
                  dataset={s.dataset}
                  label="Never use"
                  hint="blacklist"
                  value={s.exclude}
                  onChange={(v) => set("exclude", v)}
                />
              </div>
            </details>
            <button className="t-button" onClick={run} disabled={busy || presentCount === 0}>
              {busy ? "Searching…" : "Search"}
            </button>
            {presentCount === 0 && <p className="warn">Select at least one town NPC.</p>}
            {error && <p className="warn">{error}</p>}
          </section>

          {resp && (
            <section className="results">
              <p className="muted stats">
                {resp.version}: {resp.moves_available} possible moves · {resp.stats.solves} LP solves
                in {resp.stats.elapsed.toFixed(2)}s
                {!resp.stats.complete && (
                  <span className="warn"> · stopped at the limit, results may not be optimal</span>
                )}
              </p>
              {resp.loops.map((l, i) => (
                <div className="panel empty" key={`loop${i}`}>
                  Infinite money loop (no purchase needed, repeatable forever):{" "}
                  {l.moves.map((m) => m.text).join("; ")}
                </div>
              ))}
              {resp.results.length === 0 && (
                <div className="panel empty">
                  No profitable recipe. Try other NPCs, progression flags or biomes, or tick
                  "show losing chains too".
                </div>
              )}
              {resp.results.map((r, i) => (
                <ResultCard key={i} result={r} rank={i + 1} />
              ))}
            </section>
          )}
          </>
          )}
        </main>
      </div>
    </div>
  );
}
