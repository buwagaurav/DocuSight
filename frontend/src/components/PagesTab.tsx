"use client";

import { ChevronLeft, ChevronRight, Download, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Doc } from "@/lib/api";
import { Badge, Button, Card, Spinner, cn } from "./ui";

function colName(i: number) {
  let s = "";
  for (i += 1; i > 0; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + ((i - 1) % 26)) + s;
  return s;
}

function SheetView({ doc }: { doc: Doc }) {
  const [idx, setIdx] = useState(0);
  const [loaded, setLoaded] = useState<{ idx: number; name: string; rows: string[][]; total_rows: number; total_cols: number } | null>(null);
  const grid = loaded?.idx === idx ? loaded : null;
  useEffect(() => {
    api
      .sheet(doc.id, idx)
      .then((g) => setLoaded({ idx, ...g }))
      .catch(() => setLoaded(null));
  }, [doc.id, idx]);
  const meta = doc.sheets[idx];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {doc.sheets.map((s) => (
          <button
            key={s.index}
            onClick={() => setIdx(s.index)}
            className={cn(
              "rounded-lg border px-3 py-1.5 text-sm font-medium",
              s.index === idx ? "border-accent bg-accent-soft text-accent-ink" : "border-line bg-panel text-ink-2 hover:bg-panel-2",
            )}
          >
            {s.name}
          </button>
        ))}
      </div>
      {meta && (
        <div className="flex flex-wrap gap-1.5">
          <Badge>
            {meta.rows.toLocaleString()} rows × {meta.cols} cols
          </Badge>
          <Badge tone="accent">{meta.tables.length} table(s) detected</Badge>
          {!!meta.merged && <Badge>{meta.merged} merged ranges</Badge>}
          {!!meta.formulas && <Badge>{meta.formulas} formulas</Badge>}
          {!!meta.hidden && <Badge tone="warn">hidden sheet</Badge>}
        </div>
      )}
      {!grid ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-6 w-6 text-accent" />
        </div>
      ) : (
        <div className="max-h-[620px] overflow-auto rounded-lg border border-line bg-panel">
          <table className="border-collapse text-[12.5px]">
            <thead className="sticky top-0 z-10">
              <tr>
                <th className="sticky left-0 z-20 w-10 border border-line bg-panel-2" />
                {grid.rows[0]?.map((_, j) => (
                  <th key={j} className="min-w-[80px] border border-line bg-panel-2 px-2 py-1 text-center text-[11px] font-medium text-muted">
                    {colName(j)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.rows.map((r, i) => (
                <tr key={i}>
                  <td className="sticky left-0 border border-line bg-panel-2 px-2 py-1 text-right text-[11px] text-muted">{i + 1}</td>
                  {r.map((v, j) => (
                    <td
                      key={j}
                      title={v}
                      className={cn("max-w-[260px] truncate whitespace-nowrap border border-line px-2 py-1 text-ink", /^-?[\d.,]+%?$/.test(v) && "text-right tabular-nums")}
                    >
                      {v}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {grid.total_rows > grid.rows.length && (
            <div className="px-3 py-2 text-xs text-muted">
              Showing the first {grid.rows.length} of {grid.total_rows.toLocaleString()} rows — the Data tab and chat cover all rows.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function PagesTab({ doc, page, setPage }: { doc: Doc; page: number | null; setPage: (p: number | null) => void }) {
  const [zoom, setZoom] = useState(1);
  if (doc.kind !== "pdf") return <SheetView doc={doc} />;

  if (page) {
    return (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => setPage(null)}>
            All pages
          </Button>
          <Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page">
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-sm tabular-nums text-ink-2">
            Page {page} / {doc.n_pages}
          </span>
          <Button size="sm" disabled={page >= doc.n_pages} onClick={() => setPage(page + 1)} aria-label="Next page">
            <ChevronRight className="h-4 w-4" />
          </Button>
          <div className="ml-auto flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={() => setZoom(Math.max(0.5, zoom - 0.25))} aria-label="Zoom out">
              <ZoomOut className="h-4 w-4" />
            </Button>
            <span className="w-10 text-center text-xs tabular-nums text-ink-2">{Math.round(zoom * 100)}%</span>
            <Button size="sm" variant="ghost" onClick={() => setZoom(Math.min(2.5, zoom + 0.25))} aria-label="Zoom in">
              <ZoomIn className="h-4 w-4" />
            </Button>
            <a href={api.fileUrl(doc.id)} target="_blank" rel="noreferrer">
              <Button size="sm" variant="ghost">
                <Download className="h-4 w-4" /> Original
              </Button>
            </a>
          </div>
        </div>
        <div className="overflow-auto rounded-xl border border-line bg-panel-2 p-4">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={api.pageUrl(doc.id, page)}
            alt={`Page ${page}`}
            className="mx-auto rounded bg-white shadow-md"
            style={{ width: `${Math.round(zoom * 100)}%`, maxWidth: "none" }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
      {Array.from({ length: doc.n_pages }, (_, i) => i + 1).map((p) => (
        <button key={p} onClick={() => setPage(p)} className="group text-left">
          <Card className="overflow-hidden p-1.5 transition-shadow group-hover:border-accent group-hover:shadow-md">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={api.pageUrl(doc.id, p)} alt={`Page ${p}`} loading="lazy" className="aspect-[3/4] w-full rounded bg-white object-contain object-top" />
          </Card>
          <div className="mt-1 text-center text-xs text-ink-2">
            Page {p}
            {doc.tables.some((t) => t.page === p) && <span className="ml-1 text-accent">· table</span>}
          </div>
        </button>
      ))}
    </div>
  );
}
