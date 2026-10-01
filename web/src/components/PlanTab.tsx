import { useEffect, useRef, useState } from "react";
import {
  api,
  type PlanRequest,
  type PlanResponse,
  type PlanResult,
  type StockItem,
  type WorldSummary,
  type WorldDetail,
} from "../api";

interface Props {
  dataset: string;
  flags: string[];
  shimmer: boolean;
  discount: boolean;
  npcs: Record<number, { adjustment: number; zones: string[] | "any" }>;
}

/**
 * The plan tab: pick one of your saved worlds, uncheck any chest items you would rather
 * keep, and let the engine find the most profitable things to do with the rest (with or
 * without farming). Stock selection is the "who placed this chest" problem: the save
 * format does not record provenance, so every chest counts by design.
 */
export function PlanTab(props: Props) {
  const [worlds, setWorlds] = useState<WorldSummary[]>([]);
  const [detail, setDetail] = useState<WorldDetail | null>(null);
  const [worldPath, setWorldPath] = useState<string | null>(null);
  const [stock, setStock] = useState<Record<number, number>>({});
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [withFarming, setWithFarming] = useState(false);
  const [walletText, setWalletText] = useState("0");
  const [top, setTop] = useState(5);
  const [busy, setBusy] = useState(false);
  const [resp, setResp] = useState<PlanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requirement = useRef<string | null>(null);

  useEffect(() => {
    api.worlds().then(setWorlds, (e) => setError(String(e.message ?? e)));
  }, []);

  const pickWorld = async (path: string | null) => {
    setWorldPath(path);
    setDetail(null);
    setStock({});
    setResp(null);
    if (!path) {
      api.worlds().then(setWorlds, (e) => setError(String(e.message ?? e)));
      return;
    }
    setBusy(true);
    try {
      const d = await api.worldDetail(path, props.dataset);
      setDetail(d);
      const initial: Record<number, number> = {};
      d.stock.forEach((si: StockItem) => {
        initial[si.id] = si.count;
      });
      setStock(initial);
      setChecked(new Set(d.stock.map((si) => si.id)));
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const run = async () => {
    const picked: Record<number, number> = {};
    checked.forEach((id) => {
      if (stock[id]) picked[id] = stock[id];
    });
    const wallet = walletText.trim()
      .split(/\s+/)
      .map((t) => {
        const m = t.match(/^(\d+)(p|g|s|c)?$/i);
        if (!m) return 0;
        const unit = ({ p: 1_000_000, g: 10_000, s: 100, c: 1 } as Record<string, number>)[
          m[2]?.toLowerCase() ?? "c"
        ];
        return +m[1] * (unit ?? 1);
      })
      .reduce((a, b) => a + b, 0);
    const req: PlanRequest = {
      dataset: props.dataset,
      world: worldPath,
      stock: picked,
      npcs: props.npcs,
      flags: props.flags,
      shimmer: props.shimmer,
      discount: props.discount,
      kinds: ["buy", "craft", "shimmer"],
      include: null,
      exclude: [],
      must_use: [],
      top_n: top,
      wallet,
      kills: withFarming ? 300 : 0,
      expert: withFarming,
      min_profit: 0,
      show_liquidation: false,
      time_limit: 90,
    };
    setBusy(true);
    try {
      requirement.current = worldPath;
      setResp(await api.plan(req));
      setError(null);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
      requirement.current = null;
    }
  };

  const toggle = (id: number) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const fmtCount = (n: number) =>
    n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;

  return (
    <section className="panel plan-panel">
      <h2>Plan from your world</h2>
      <p className="muted">
        Every item lying in any chest of a world becomes stock (nothing to install: the file is
        only read). Uncheck items you would rather keep; the engine finds the most profitable
        plans from the rest.
      </p>

      <div className="row">
        <select
          className="t-input"
          value={worldPath ?? ""}
          onChange={(e) => pickWorld(e.target.value || null)}
        >
          <option value="">-- pick a world --</option>
          {worlds.map((w) => (
            <option key={w.path} value={w.path}>
              {w.name}
              {w.error ? " (unreadable)" : ""}
              {" · "}#{w.path ? w.path.split(/[\\/]/).pop() : ""}
            </option>
          ))}
        </select>
      </div>

      {detail && (
        <div className="plan-world-info">
          <b>{detail.name}</b> (seed {detail.seed}) — {detail.npcs.length} town NPCs,{" "}
          {detail.stock.length} distinct items in {detail.chests.length} chests
        </div>
      )}

      {detail && (
        <details open className="constraints">
          <summary>Use as stock ({checked.size}/{detail.stock.length} items)</summary>
          <div className="sprite-grid plan-stock-grid">
            {detail.stock.map((si) => {
              const on = checked.has(si.id);
              return (
                <label
                  key={si.id}
                  className={`sprite-cell plan-stock-cell ${on ? "" : "off"}`}
                  title={`${si.name}: ${si.count}`}
                >
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => toggle(si.id)}
                  />
                  <span className="sprite-name">
                    {si.name}
                  </span>
                  <span className="muted small">×{fmtCount(si.count)}</span>
                </label>
              );
            })}
          </div>
        </details>
      )}

      <div className="row">
        <label className="check">
          <input
            type="checkbox"
            checked={withFarming}
            onChange={(e) => setWithFarming(e.target.checked)}
          />
          <span>with farming (adds recipes that need killing, EV per kill)</span>
        </label>
      </div>

      <div className="row">
        <div className="field">
          <label className="field-label">Wallet to spend on buys</label>
          <input
            className="t-input small"
            type="text"
            placeholder="e.g. 50g"
            value={walletText}
            onChange={(e) => setWalletText(e.target.value)}
          />
        </div>
        <div className="field">
          <label className="field-label">Plans to show</label>
          <input
            className="t-input small"
            type="number"
            min={1}
            max={20}
            value={top}
            onChange={(e) => setTop(Math.max(1, +e.target.value | 0))}
          />
        </div>
      </div>

      <button
        className="t-button"
        onClick={run}
        disabled={busy || Object.keys(stock).length < 1}
      >
        {busy ? "Planning…" : "Plan"}
      </button>

      {error && <p className="warn">{error}</p>}

      {resp && (
        <section className="results">
          <p className="muted stats">
            {resp.version}: liquidation floor {fmtCount(resp.liquidation)}c — doing nothing but
            selling everything · {resp.stats.solves} LPs in {resp.stats.elapsed.toFixed(2)}s
          </p>
          {resp.results.length === 0 && (
            <div className="panel empty">
              Every plan loses coins on this stock. Farm some more materials, raise the
              wallet, or sell. (The floor assumes sale at the happiest NPC's price.)
            </div>
          )}
          {resp.results.map((r, i) => (
            <PlanResultCard key={i} result={r} rank={i + 1} />
          ))}
        </section>
      )}
    </section>
  );
}

const KIND_LABEL: Record<string, string> = {
  buy: "Buy",
  craft: "Craft",
  shimmer: "Shimmer",
  sell: "Sell",
  stock: "Take",
  farm: "Farm",
};

function PlanResultCard({ result, rank }: { result: PlanResult; rank: number }) {
  return (
    <article className="panel result">
      <header className="result-head">
        <span className="rank">#{rank}</span>
        <span className="gain pos">+ {result.profit_text}</span>
        <span className="result-meta">
          spend {result.capital_text}
          {result.kills > 0 && ` · ~${result.kills} kills`}
        </span>
        {result.approx && (
          <span className="badge" title="per-kill expected values; results vary">
            approx
          </span>
        )}
      </header>
      <div className="steps">
        {result.moves.map((m, i) => (
          <div className={`step step-${m.kind}`} key={i}>
            <div className="step-head">
              <span className="step-num">{i + 1}</span>
              <span className={`badge badge-${m.kind}`}>
                {KIND_LABEL[m.kind] ?? m.kind}
              </span>
              {m.npc && <span className="step-npc">{m.npc}</span>}
            </div>
            <div className="step-body">
              {m.kind === "buy" && <span>{result.capital_text}</span>}
              {(m.inputs ?? []).map((s, j) => (
                <span key={`i${j}`} className="slot-mini">{s.count}× {s.name}</span>
              ))}
              {(m.outputs ?? []).length > 0 && <span className="arrow">➜</span>}
              {(m.outputs ?? []).map((s, j) => (
                <span key={`o${j}`} className="slot-mini">{s.count}× {s.name}</span>
              ))}
              {m.kind === "sell" && <span> {m.text}</span>}
            </div>
            <div className="step-text">{m.text}</div>
          </div>
        ))}
      </div>
      {result.residue.length > 0 && (
        <footer className="leftover">
          <span className="field-label">Residue sold</span>
          {result.residue.map((s) => (
            <span key={s.id} className="slot-mini">
              {s.count}× {s.name}
            </span>
          ))}
        </footer>
      )}
    </article>
  );
}
