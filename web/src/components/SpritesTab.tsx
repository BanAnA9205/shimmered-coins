import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SpritePreview, type SpritesStatus } from "../api";

export function SpritesTab({ dataset }: { dataset: string }) {
  const [status, setStatus] = useState<SpritesStatus | null>(null);
  const [preview, setPreview] = useState<SpritePreview[]>([]);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(0);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [manualPath, setManualPath] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    setStatus(await api.sprites());
    setPreview(await api.spritePreview(dataset));
  }, [dataset]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const startImport = useCallback(
    async (path?: string) => {
      setError(null);
      setDone(0);
      setTotal(0);
      try {
        await api.spriteImport(path);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return;
      }
      setBusy(true);
      const poll = async () => {
        const st = await api.spriteImportStatus();
        setDone(st.done);
        setTotal(st.total);
        if (st.error) setError(st.error);
        if (st.running) {
          timer.current = window.setTimeout(poll, 400);
        } else {
          setBusy(false);
          refresh();
        }
      };
      poll();
    },
    [refresh],
  );

  const pct = total > 0 ? Math.round((done / total) * 100) : busy ? 0 : 100;

  return (
    <section className="panel sprites-panel">
      <h2>Item sprites</h2>
      <p className="muted">
        {status
          ? status.installed > 0
            ? `${status.installed.toLocaleString()} sprites imported.`
            : "No sprites yet: results show item initials instead."
          : "scanning…"}
      </p>

      {busy && (
        <div className="progress">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
          <span className="progress-label">
            importing {done.toLocaleString()} / {total.toLocaleString()}
          </span>
        </div>
      )}
      {error && <p className="warn">{error}</p>}

      <div className="sprite-sources">
        {status?.sources.map((s) => (
          <div className="sprite-source" key={s.path}>
            <div className="sprite-source-info">
              <span className="badge">{s.app}</span>
              <span className="muted">{s.path}</span>
              <span className="muted"> · {s.items.toLocaleString()} item sprites</span>
            </div>
            <button className="t-button" disabled={busy} onClick={() => startImport(s.path)}>
              Import
            </button>
          </div>
        ))}
      </div>
      {status && status.sources.length === 0 && (
        <p className="muted">
          No Terraria or tModLoader install found automatically. Enter the game's
          <code> Content/Images</code> folder by hand:
        </p>
      )}

      <div className="sprite-manual">
        <input
          className="t-input"
          type="text"
          placeholder="/path/to/Terraria/Content/Images"
          value={manualPath}
          onChange={(e) => setManualPath(e.target.value)}
        />
        <button
          className="t-button"
          disabled={busy || !manualPath.trim()}
          onClick={() => startImport(manualPath.trim())}
        >
          Import from folder
        </button>
      </div>

      <p className="muted small">
        Sprites are Re-Logic's game art: they are imported from your own install, stay local in
        <code> web/public/sprites/</code> and are never committed.
      </p>

      {preview.length > 0 && (
        <>
          <h3>Preview</h3>
          <div className="sprite-grid">
            {preview.map((p) => (
              <div className="sprite-cell" key={p.id} title={p.name}>
                <img src={p.url} alt={p.name} loading="lazy" />
                <span className="sprite-name">{p.name}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
