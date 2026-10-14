"use client";

import { ArrowDown, ArrowUp, BarChart3, Database, Download, Play, Search, Table2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type Doc, type TableMeta } from "@/lib/api";
import ChartView from "./ChartView";
import { Badge, Button, Card, Empty, Spinner, cn, download, fmtCell, toCSV } from "./ui";

const PAGE = 100;

export function DataGrid({ columns, rows, height = 480 }: { columns: string[]; rows: unknown[][]; height?: number }) {
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const [filter, setFilter] = useState("");
  const [pageRaw, setPage] = useState(0);

  const view = useMemo(() => {
    const q = filter.toLowerCase();
    let r = q ? rows.filter((row) => row.some((v) => String(v ?? "").toLowerCase().includes(q))) : rows;
    if (sort) {
      r = [...r].sort((a, b) => {
        const x = a[sort.col], y = b[sort.col];
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * sort.dir;
      });
    }
    return r;
  }, [rows, filter, sort]);

  const pages = Math.max(1, Math.ceil(view.length / PAGE));
  const page = Math.min(pageRaw, pages - 1);
  const slice = view.slice(page * PAGE, page * PAGE + PAGE);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-muted" />
          <input
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setPage(0);
            }}
            placeholder="Filter rows…"
            className="h-8 w-56 rounded-lg border border-line bg-panel pl-8 pr-2 text-sm text-ink outline-none focus:border-accent"
          />
        </div>
        <span className="text-xs text-muted">
          {view.length.toLocaleString()} row{view.length === 1 ? "" : "s"}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={() => download("table.csv", toCSV(columns, view), "text/csv")}>
            <Download className="h-3.5 w-3.5" /> CSV
          </Button>
          {pages > 1 && (
            <>
              <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                ‹
              </Button>
              <span className="px-1 text-xs tabular-nums text-ink-2">
                {page + 1}/{pages}
              </span>
              <Button size="sm" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>
                ›
              </Button>
            </>
          )}
        </div>
      </div>
      <div className="overflow-auto rounded-lg border border-line" style={{ maxHeight: height }}>
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 z-10 bg-panel-2">
            <tr>
              <th className="w-10 border-b border-line px-2 py-2 text-right text-[11px] font-medium text-muted">#</th>
              {columns.map((c, i) => (
                <th
                  key={c}
                  onClick={() => setSort(sort?.col === i ? (sort.dir === 1 ? { col: i, dir: -1 } : null) : { col: i, dir: 1 })}
                  className="cursor-pointer select-none whitespace-nowrap border-b border-line px-3 py-2 text-left font-semibold text-ink hover:bg-panel"
                >
                  <span className="inline-flex items-center gap-1">
                    {c}
                    {sort?.col === i && (sort.dir === 1 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {slice.map((r, i) => (
              <tr key={i} className="hover:bg-panel-2">
                <td className="border-b border-line px-2 py-1.5 text-right text-[11px] tabular-nums text-muted">{page * PAGE + i + 1}</td>
                {r.map((v, j) => (
                  <td
                    key={j}
                    className={cn("max-w-[320px] truncate whitespace-nowrap border-b border-line px-3 py-1.5 text-ink", typeof v === "number" && "text-right tabular-nums")}
                    title={String(v ?? "")}
                  >
                    {fmtCell(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function QuickChart({ table, columns, rows }: { table: TableMeta; columns: string[]; rows: unknown[][] }) {
  const numeric = table.columns.filter((c) => c.sum !== undefined).map((c) => c.name);
  const cats = table.columns.filter((c) => c.sum === undefined).map((c) => c.name);
  const [x, setX] = useState(cats[0] ?? columns[0]);
  const [y, setY] = useState(numeric[0] ?? "");
  const [agg, setAgg] = useState<"sum" | "avg" | "count">("sum");
  const [type, setType] = useState<"bar" | "line" | "pie">("bar");
  if (!numeric.length) return <p className="text-sm text-muted">No numeric columns to chart.</p>;
  const xi = columns.indexOf(x), yi = columns.indexOf(y);
  const groups = new Map<string, { s: number; n: number }>();
  for (const r of rows) {
    const k = fmtCell(r[xi]) || "(blank)";
    const g = groups.get(k) ?? { s: 0, n: 0 };
    const v = Number(r[yi]);
    if (!Number.isNaN(v)) g.s += v;
    g.n += 1;
    groups.set(k, g);
  }
  let data = [...groups.entries()].map(([k, g]) => ({ [x]: k, [y]: agg === "sum" ? g.s : agg === "avg" ? g.s / g.n : g.n }));
  if (data.length > 30) data = data.sort((a, b) => Number(b[y]) - Number(a[y])).slice(0, 30);
  const sel = "h-8 rounded-lg border border-line bg-panel px-2 text-sm text-ink";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm text-ink-2">
        <select className={sel} value={type} onChange={(e) => setType(e.target.value as typeof type)} aria-label="Chart type">
          <option value="bar">Bar</option>
          <option value="line">Line</option>
          <option value="pie">Donut</option>
        </select>
        <select className={sel} value={agg} onChange={(e) => setAgg(e.target.value as typeof agg)} aria-label="Aggregation">
          <option value="sum">Sum of</option>
          <option value="avg">Average of</option>
          <option value="count">Count of</option>
        </select>
        <select className={sel} value={y} onChange={(e) => setY(e.target.value)} aria-label="Value column">
          {numeric.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
        by
        <select className={sel} value={x} onChange={(e) => setX(e.target.value)} aria-label="Group column">
          {columns.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </div>
      <ChartView spec={{ chart_type: type, title: `${agg === "count" ? "Count" : agg === "avg" ? "Average" : "Total"} ${agg === "count" ? "" : y} by ${x}`, data, x_key: x, y_keys: [y] }} height={320} />
    </div>
  );
}

export default function DataTab({ doc }: { doc: Doc }) {
  const [active, setActiveRaw] = useState<string | null>(doc.tables[0]?.name ?? null);
  const [loaded, setLoaded] = useState<{ key: string; columns: string[]; rows: unknown[][]; total: number } | null>(null);
  const data = loaded && loaded.key === active ? loaded : null;
  const [mode, setMode] = useState<"grid" | "chart" | "sql">("grid");
  const [sql, setSql] = useState(active ? `SELECT *\nFROM "${active}"\nLIMIT 50` : "");
  const [sqlRes, setSqlRes] = useState<{ columns: string[]; rows: unknown[][] } | null>(null);
  const [sqlErr, setSqlErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const table = doc.tables.find((t) => t.name === active) ?? null;

  const setActive = (name: string) => {
    setActiveRaw(name);
    setSql(`SELECT *\nFROM "${name}"\nLIMIT 50`);
    setSqlRes(null);
  };

  useEffect(() => {
    if (!active) return;
    api
      .table(doc.id, active, 5000)
      .then((r) => setLoaded({ key: active, ...r }))
      .catch(() => setLoaded({ key: active, columns: [], rows: [], total: 0 }));
  }, [doc.id, active]);

  if (!doc.tables.length) {
    return (
      <Empty icon={<Database className="h-8 w-8" />} title="No structured tables found">
        This document&apos;s content is indexed as text and page images — ask questions in the chat.
      </Empty>
    );
  }

  const runSql = async () => {
    setBusy(true);
    setSqlErr(null);
    try {
      setSqlRes(await api.sql(doc.id, sql));
    } catch (e) {
      setSqlErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
      <div className="space-y-1.5">
        {doc.tables.map((t) => (
          <button
            key={t.name}
            onClick={() => setActive(t.name)}
            className={cn(
              "w-full rounded-lg border px-3 py-2 text-left transition-colors",
              t.name === active ? "border-accent bg-accent-soft" : "border-line bg-panel hover:bg-panel-2",
            )}
          >
            <div className="truncate text-sm font-medium text-ink" title={t.title}>
              {t.title}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted">
              <code className="truncate">{t.name}</code> · {t.n_rows.toLocaleString()}×{t.n_cols}
            </div>
          </button>
        ))}
      </div>

      <Card className="min-w-0 p-4">
        {table && (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="font-semibold text-ink">{table.title}</div>
              <div className="text-xs text-muted">
                from {table.source} · SQL name <code className="text-ink-2">{table.name}</code>
              </div>
            </div>
            <div className="flex rounded-lg border border-line p-0.5">
              {(
                [
                  ["grid", Table2, "Data"],
                  ["chart", BarChart3, "Visualize"],
                  ["sql", Database, "SQL"],
                ] as const
              ).map(([m, Icon, label]) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={cn("flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium", mode === m ? "bg-accent text-white" : "text-ink-2 hover:bg-panel-2")}
                >
                  <Icon className="h-3.5 w-3.5" /> {label}
                </button>
              ))}
            </div>
          </div>
        )}

        {table && mode !== "sql" && (
          <div className="mb-3 flex flex-wrap gap-1.5">
            {table.columns.map((c) => (
              <Badge key={c.name} tone={c.sum !== undefined ? "accent" : "neutral"}>
                <span title={c.sum !== undefined ? `min ${c.min} · max ${c.max} · mean ${c.mean} · sum ${c.sum}` : c.top?.join(", ")}>
                  {c.name} <span className="opacity-60">{c.type.replace("64", "").replace("[us]", "")}</span>
                </span>
              </Badge>
            ))}
          </div>
        )}

        {!data ? (
          <div className="flex justify-center py-16">
            <Spinner className="h-6 w-6 text-accent" />
          </div>
        ) : mode === "grid" ? (
          <DataGrid columns={data.columns} rows={data.rows} />
        ) : mode === "chart" && table ? (
          <QuickChart key={table.name} table={table} columns={data.columns} rows={data.rows} />
        ) : (
          <div className="space-y-3">
            <textarea
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runSql();
              }}
              spellCheck={false}
              rows={5}
              className="w-full rounded-lg border border-line bg-panel-2 p-3 font-mono text-[13px] text-ink outline-none focus:border-accent"
              aria-label="SQL query"
            />
            <div className="flex items-center gap-2">
              <Button variant="primary" size="sm" onClick={runSql} disabled={busy}>
                {busy ? <Spinner className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />} Run (⌘↵)
              </Button>
              <span className="text-xs text-muted">Read-only DuckDB · tables: {doc.tables.map((t) => t.name).join(", ")}</span>
            </div>
            {sqlErr && <div className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">{sqlErr}</div>}
            {sqlRes && <DataGrid columns={sqlRes.columns} rows={sqlRes.rows} height={360} />}
          </div>
        )}
      </Card>
    </div>
  );
}
