import { useEffect, useRef, useState } from "react";
import { api, type ItemRef } from "../api";

/** Autocomplete over the dataset's items; picked items show as removable chips. */
export function ItemPicker({
  dataset,
  label,
  hint,
  value,
  onChange,
}: {
  dataset: string;
  label: string;
  hint: string;
  value: ItemRef[];
  onChange: (items: ItemRef[]) => void;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<ItemRef[]>([]);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (!q.trim()) {
      setHits([]);
      return;
    }
    timer.current = window.setTimeout(() => {
      api.items(dataset, q).then(setHits, () => setHits([]));
    }, 150);
  }, [q, dataset]);

  const add = (it: ItemRef) => {
    if (!value.some((v) => v.id === it.id)) onChange([...value, it]);
    setQ("");
    setHits([]);
  };

  return (
    <div className="picker">
      <label className="field-label" title={hint}>
        {label}
      </label>
      <div className="chips">
        {value.map((it) => (
          <button
            key={it.id}
            className="chip"
            title="remove"
            onClick={() => onChange(value.filter((v) => v.id !== it.id))}
          >
            {it.name} ✕
          </button>
        ))}
      </div>
      <input
        className="t-input"
        placeholder="search items…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && hits[0]) add(hits[0]);
        }}
      />
      {hits.length > 0 && (
        <ul className="picker-hits">
          {hits.map((it) => (
            <li key={it.id}>
              <button onClick={() => add(it)}>{it.name}</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
