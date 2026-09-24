import { useState } from "react";

function initials(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  if (words.length === 1) return words[0].slice(0, 2);
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/**
 * An inventory slot. Shows `/sprites/Item_<id>.png` when it exists (sprites are imported from
 * your local game files and never committed), otherwise the item's initials.
 */
export function ItemSlot({ id, name, count }: { id: number; name: string; count?: number }) {
  const [broken, setBroken] = useState(false);
  return (
    <div className="slot" title={count !== undefined ? `${count} × ${name}` : name}>
      {broken ? (
        <span className="slot-initials">{initials(name)}</span>
      ) : (
        <img
          className="slot-sprite"
          src={`/sprites/Item_${id}.png`}
          alt={name}
          onError={() => setBroken(true)}
        />
      )}
      {count !== undefined && count !== 1 && <span className="slot-count">{count}</span>}
    </div>
  );
}
