// Ranked horizontal bars: one series, one hue, largest first (dataviz: "compare the parts, rank them").
// Marks <= 24px thick with a 4px rounded data-end, square at the baseline; values at the bar tip in text ink;
// a hover/focus tooltip per bar; the same numbers in a table for screen readers and exact reading.

// Validated against the white card surface (validate_palette.js: all checks pass).
const BAR = "#2a78d6";

export type Bar = { label: string; value: number };

export function RankedBars({ title, bars, unit = "" }: { title: string; bars: Bar[]; unit?: string }) {
  const sorted = [...bars].filter((b) => b.value > 0).sort((a, b) => b.value - a.value);
  const max = Math.max(1, ...sorted.map((b) => b.value));
  const total = sorted.reduce((sum, b) => sum + b.value, 0);
  if (sorted.length === 0) return <p className="text-sm text-muted-foreground">Nothing to show.</p>;

  return (
    <figure className="space-y-3">
      <ul className="space-y-2" aria-hidden>
        {sorted.map((bar) => (
          <li key={bar.label} className="grid grid-cols-[minmax(7rem,12rem)_1fr] items-center gap-3 text-sm">
            <span className="truncate text-muted-foreground" title={bar.label}>{bar.label}</span>
            <div className="group relative flex items-center gap-2 border-l border-border" tabIndex={0}>
              <div className="h-[18px] rounded-r-[4px] transition-opacity group-hover:opacity-85"
                   style={{ width: `${(bar.value / max) * 85}%`, background: BAR, minWidth: 2 }} />
              <span className="tabular-nums text-foreground">{bar.value.toLocaleString()}{unit}</span>
              <span role="tooltip"
                    className="pointer-events-none absolute -top-9 left-0 z-10 hidden whitespace-nowrap rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-sm group-hover:block group-focus:block">
                {bar.label}: {bar.value.toLocaleString()}{unit} · {((bar.value / total) * 100).toFixed(1)}% of {title.toLowerCase()}
              </span>
            </div>
          </li>
        ))}
      </ul>
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer">Show as table</summary>
        <table className="mt-2 w-full text-left">
          <caption className="sr-only">{title}</caption>
          <thead><tr><th scope="col" className="py-1">Category</th><th scope="col" className="py-1 text-right">Count</th></tr></thead>
          <tbody>
            {sorted.map((bar) => (
              <tr key={bar.label} className="border-t">
                <td className="py-1">{bar.label}</td>
                <td className="py-1 text-right tabular-nums">{bar.value.toLocaleString()}{unit}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** A single ratio against a whole, on a track from the same ramp (dataviz: "meter", not a two-slice pie). */
export function Meter({ label, value, total, detail }: { label: string; value: number; total: number; detail?: string }) {
  const share = total ? value / total : 0;
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-semibold tabular-nums">{(share * 100).toFixed(1)}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full" style={{ background: "#cde2fb" }} role="meter"
           aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={value}
           aria-valuetext={`${value.toLocaleString()} of ${total.toLocaleString()}`}>
        <div className="h-full rounded-full" style={{ width: `${share * 100}%`, background: BAR }} />
      </div>
      {detail && <p className="text-xs text-muted-foreground">{detail}</p>}
    </div>
  );
}
