type Series = { name: string; color: string; values: (number | null)[] };

/** A small SVG line chart: x is seconds, y is auto-scaled. */
export function LineChart({ title, unit, length, series }: { title: string; unit: string; length: number; series: Series[] }) {
  const W = 520, H = 200, L = 48, R = 12, T = 28, B = 24;
  const all = series.flatMap((s) => s.values).filter((v): v is number => v != null);
  const ymax = niceMax(Math.max(1, ...all));
  const xmax = Math.max(1, length - 1);
  const px = (i: number) => L + (i / xmax) * (W - L - R);
  const py = (v: number) => H - B - (v / ymax) * (H - T - B);
  return (
    <div className="card p-3">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        <text x={L} y={16} fill="#e8ebf2" fontSize="12" fontWeight="600">{title}</text>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={L} x2={W - R} y1={py(ymax * f)} y2={py(ymax * f)} stroke="#262d3d" />
            <text x={L - 6} y={py(ymax * f) + 4} fill="#8b94a8" fontSize="10" textAnchor="end">{fmt(ymax * f)}</text>
          </g>
        ))}
        <text x={W - R} y={H - 6} fill="#8b94a8" fontSize="10" textAnchor="end">seconds · {unit}</text>
        {series.map((s) => {
          let d = "";
          s.values.forEach((v, i) => { if (v != null) d += `${d ? "L" : "M"}${px(i).toFixed(1)},${py(v).toFixed(1)} `; });
          return d ? <path key={s.name} d={d} fill="none" stroke={s.color} strokeWidth="2" /> : null;
        })}
      </svg>
      <div className="flex gap-4 px-2 text-xs text-muted">
        {series.map((s) => <span key={s.name}><span style={{ color: s.color }}>●</span> {s.name}</span>)}
      </div>
    </div>
  );
}

function niceMax(v: number) {
  const m = 10 ** Math.floor(Math.log10(v));
  return ([1, 2, 5, 10].find((s) => v <= s * m) ?? 10) * m;
}
const fmt = (v: number) => (v >= 1000 ? `${v / 1000}k` : String(+v.toFixed(1)));
