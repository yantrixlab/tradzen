import type { Shape } from '../lib/patterns';

// ------------------------------------------------------------------ a little drawing of the candle a step describes
const GEOM: Record<Shape, [number, number, number, number]> = {      // body top, body bottom, wick top, wick bottom  (box is 26 x 44)
  any: [14, 28, 7, 38], hammer: [8, 16, 6, 40], star: [28, 36, 4, 38], doji: [21, 23, 5, 39], strong: [8, 36, 6, 38], weak: [19, 25, 5, 39], long: [5, 39, 2, 42],
};
export function CandleIcon({ color, shape, count = 1 }: { color: string; shape: Shape; count?: number }) {
  const col = color === 'green' ? '#22c55e' : color === 'red' ? '#ef4444' : '#8a93b2';
  const [bt, bb, wt, wb] = GEOM[shape];
  const n = Math.min(3, Math.max(1, count));
  return (
    <svg width={26 * n + (n > 1 ? 4 : 0)} height={44} className="shrink-0" aria-hidden>
      {Array.from({ length: n }, (_, k) => (
        <g key={k} transform={`translate(${k * 28},0)`}>
          <line x1={13} x2={13} y1={wt} y2={wb} stroke={col} strokeWidth={2} />
          <rect x={5} y={bt} width={16} height={Math.max(2, bb - bt)} fill={col} rx={1.5} />
        </g>
      ))}
    </svg>
  );
}

