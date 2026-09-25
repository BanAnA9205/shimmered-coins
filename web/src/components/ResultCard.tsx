import type { MoveOut, ResultOut } from "../api";
import { Coins } from "./Coins";
import { ItemSlot } from "./ItemSlot";

const KIND_LABEL: Record<MoveOut["kind"], string> = {
  buy: "Buy",
  craft: "Craft",
  shimmer: "Shimmer",
  sell: "Sell",
};

function Step({ move, index }: { move: MoveOut; index: number }) {
  return (
    <div className={`step step-${move.kind}`}>
      <div className="step-head">
        <span className="step-num">{index + 1}</span>
        <span className={`badge badge-${move.kind}`}>{KIND_LABEL[move.kind]}</span>
        {move.npc && <span className="step-npc">{move.npc}</span>}
      </div>
      <div className="step-body">
        {move.kind === "buy" && <Coins copper={move.cost} />}
        {move.inputs.map((s) => (
          <ItemSlot key={`i${s.id}`} {...s} />
        ))}
        <span className="arrow">➜</span>
        {move.outputs.map((s) => (
          <ItemSlot key={`o${s.id}`} {...s} />
        ))}
        {move.kind === "sell" && <Coins copper={move.revenue} />}
      </div>
      <div className="step-text">{move.text}</div>
    </div>
  );
}

export function ResultCard({ result, rank }: { result: ResultOut; rank: number }) {
  const pct = (result.gain * 100).toFixed(result.gain < 10 ? 2 : 0);
  return (
    <article className="panel result">
      <header className="result-head">
        <span className="rank">#{rank}</span>
        <span className={`gain ${result.gain >= 0 ? "pos" : "neg"}`}>
          {result.gain >= 0 ? "+" : ""}
          {pct}%
        </span>
        <span className="result-meta" title="growth per trip, ln(1+gain)/(L+c)">
          ×{result.per_trip.toFixed(2)} per trip
        </span>
        <span className="result-meta" title={result.places.join(" → ")}>
          {result.length} {result.length === 1 ? "place" : "places"}: {result.places.join(" → ")}
        </span>
        <span className="result-meta">
          capital <Coins copper={result.capital} /> · profit <Coins copper={result.profit} />
        </span>
        {result.one_shot ? (
          <span className="badge badge-loop" title="uses limited shop stock: not repeatable">
            One-shot
          </span>
        ) : (
          <span className="badge badge-loop" title="no limited-stock items: repeat forever">
            Repeatable loop
          </span>
        )}
      </header>
      <div className="steps">
        {result.moves.map((m, i) => (
          <Step key={i} move={m} index={i} />
        ))}
      </div>
      {result.leftover.length > 0 && (
        <footer className="leftover">
          <span className="field-label">Left over</span>
          {result.leftover.map((s) => (
            <ItemSlot key={s.id} {...s} />
          ))}
        </footer>
      )}
    </article>
  );
}
