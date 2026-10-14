"use client";

import { Table2 } from "lucide-react";
import { useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartSpec } from "@/lib/api";
import { fmtCell } from "./ui";

// Validated categorical palette (fixed order, never cycled). Values live in CSS vars so dark mode swaps steps.
const SERIES = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`);
const MAX_SERIES = 8;

const compact = (v: unknown) =>
  typeof v === "number" ? Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(v) : String(v);

function TooltipBox({ active, payload, label }: { active?: boolean; payload?: { name: string; value: unknown; color: string }[]; label?: unknown }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-panel px-3 py-2 text-xs shadow-lg">
      {label !== undefined && <div className="mb-1 font-semibold text-ink">{String(label)}</div>}
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2 text-ink-2">
          <span className="h-2 w-2 rounded-full" style={{ background: p.color }} />
          <span>{p.name}</span>
          <span className="ml-auto pl-3 font-medium tabular-nums text-ink">{fmtCell(p.value)}</span>
        </div>
      ))}
    </div>
  );
}

export default function ChartView({ spec, height = 280 }: { spec: ChartSpec; height?: number }) {
  const [asTable, setAsTable] = useState(false);
  const yKeys = spec.y_keys.slice(0, MAX_SERIES);
  const data = spec.data.map((row) => {
    const out: Record<string, unknown> = { ...row };
    for (const k of yKeys) {
      const v = row[k];
      if (typeof v === "string") {
        const n = Number(v.replace(/[$€£₹,%\s]/g, ""));
        if (!Number.isNaN(n)) out[k] = n;
      }
    }
    return out;
  });
  const axis = { stroke: "var(--muted)", fontSize: 11, tickLine: false, axisLine: { stroke: "var(--border)" } };
  const grid = <CartesianGrid stroke="var(--grid)" strokeDasharray="0" vertical={false} />;
  const legend = yKeys.length > 1 ? <Legend wrapperStyle={{ fontSize: 12, color: "var(--text-2)" }} iconType="circle" iconSize={8} /> : null;

  let chart: React.ReactElement;
  switch (spec.chart_type) {
    case "line":
      chart = (
        <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          {grid}
          <XAxis dataKey={spec.x_key} {...axis} />
          <YAxis {...axis} tickFormatter={compact} width={48} />
          <Tooltip content={<TooltipBox />} cursor={{ stroke: "var(--muted)", strokeDasharray: "3 3" }} />
          {legend}
          {yKeys.map((k, i) => (
            <Line key={k} type="monotone" dataKey={k} stroke={SERIES[i]} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5, stroke: "var(--panel)", strokeWidth: 2 }} />
          ))}
        </LineChart>
      );
      break;
    case "area":
      chart = (
        <AreaChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          {grid}
          <XAxis dataKey={spec.x_key} {...axis} />
          <YAxis {...axis} tickFormatter={compact} width={48} />
          <Tooltip content={<TooltipBox />} />
          {legend}
          {yKeys.map((k, i) => (
            <Area key={k} type="monotone" dataKey={k} stroke={SERIES[i]} strokeWidth={2} fill={SERIES[i]} fillOpacity={0.15} />
          ))}
        </AreaChart>
      );
      break;
    case "scatter":
      chart = (
        <ScatterChart margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--grid)" />
          <XAxis dataKey={spec.x_key} name={spec.x_key} {...axis} tickFormatter={compact} />
          <YAxis dataKey={yKeys[0]} name={yKeys[0]} {...axis} tickFormatter={compact} width={48} />
          <Tooltip content={<TooltipBox />} />
          <Scatter data={data} fill={SERIES[0]} stroke="var(--panel)" strokeWidth={1.5} />
        </ScatterChart>
      );
      break;
    case "pie": {
      const k = yKeys[0];
      const top = [...data].sort((a, b) => Number(b[k]) - Number(a[k]));
      // past 7 slices fold into "Other" rather than generating new hues
      const slices = top.length > 8 ? [...top.slice(0, 7), { [spec.x_key]: "Other", [k]: top.slice(7).reduce((s, r) => s + Number(r[k] || 0), 0) }] : top;
      chart = (
        <PieChart>
          <Tooltip content={<TooltipBox />} />
          <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" iconSize={8} />
          <Pie data={slices} dataKey={k} nameKey={spec.x_key} innerRadius="55%" outerRadius="85%" paddingAngle={1} stroke="var(--panel)" strokeWidth={2}>
            {slices.map((_, i) => (
              <Cell key={i} fill={SERIES[i]} />
            ))}
          </Pie>
        </PieChart>
      );
      break;
    }
    default:
      chart = (
        <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }} barGap={2}>
          {grid}
          <XAxis dataKey={spec.x_key} {...axis} interval="preserveStartEnd" />
          <YAxis {...axis} tickFormatter={compact} width={48} />
          <Tooltip content={<TooltipBox />} cursor={{ fill: "var(--panel-2)" }} />
          {legend}
          {yKeys.map((k, i) => (
            <Bar key={k} dataKey={k} fill={SERIES[i]} radius={[4, 4, 0, 0]} maxBarSize={44} />
          ))}
        </BarChart>
      );
  }

  return (
    <div className="rounded-xl border border-line bg-panel p-3">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-ink">{spec.title}</div>
          {spec.y_label && <div className="text-xs text-muted">{spec.y_label}</div>}
        </div>
        <button
          onClick={() => setAsTable((v) => !v)}
          className="flex items-center gap-1 rounded px-1.5 py-1 text-xs text-muted hover:bg-panel-2 hover:text-ink"
          title="Toggle table view"
        >
          <Table2 className="h-3.5 w-3.5" /> {asTable ? "Chart" : "Table"}
        </button>
      </div>
      {asTable ? (
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-xs">
            <thead>
              <tr>
                {[spec.x_key, ...yKeys].map((c) => (
                  <th key={c} className="border-b border-line px-2 py-1 text-left font-semibold text-ink-2">{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((r, i) => (
                <tr key={i}>
                  {[spec.x_key, ...yKeys].map((c) => (
                    <td key={c} className="border-b border-line px-2 py-1 tabular-nums text-ink">{fmtCell(r[c])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            {chart}
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
