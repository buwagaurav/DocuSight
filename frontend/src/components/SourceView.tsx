"use client";

import { Crosshair, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type Doc, type Highlight, type Located } from "@/lib/api";
import { Badge, Spinner, cn } from "./ui";

/** Shared pieces for showing an extracted value in its source: used by the Pages tab and the side panel. */

export function colName(i: number) {
  let s = "";
  for (i += 1; i > 0; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + ((i - 1) % 26)) + s;
  return s;
}

/** Locate `highlight` in the document; `onResult` runs once per lookup (e.g. to jump to the page). */
export function useLocated(docId: string, highlight: Highlight | null, onResult?: (r: Located) => void): Located | null {
  const [found, setFound] = useState<{ for: Highlight; located: Located } | null>(null);
  const callback = useRef(onResult);
  useEffect(() => {
    callback.current = onResult;
  });
  useEffect(() => {
    if (!highlight) return;
    let live = true;
    api
      .locate(docId, highlight.text, highlight.source)
      .then((r) => {
        if (!live) return;
        setFound({ for: highlight, located: r });
        callback.current?.(r);
      })
      .catch(() => live && setFound({ for: highlight, located: { kind: "pdf", page: null, boxes: [], status: "not_found", matched: null } }));
    return () => {
      live = false;
    };
  }, [docId, highlight]);
  return highlight && found?.for === highlight ? found.located : null;
}

function locatedMessage(doc: Doc, located: Located): string {
  const where =
    located.kind === "pdf"
      ? located.page
        ? `page ${located.page}`
        : "the document"
      : located.sheet_index != null
        ? `sheet '${doc.sheets[located.sheet_index]?.name ?? located.sheet_index + 1}'`
        : "the workbook";
  const count = located.kind === "pdf" ? located.boxes.length : located.cells.length;
  switch (located.status) {
    case "found":
      return `Highlighted on ${where}.`;
    case "ambiguous":
      return `This value appears ${count} times on ${where}. All matches are highlighted.`;
    case "partial":
      return `Highlighted the parts of this value found on ${where}.`;
    case "source_only":
      return `Exact value not found. Showing the cell the extraction cited on ${where}.`;
    default:
      if (located.kind === "sheet")
        return located.sheet_index != null
          ? `This value isn't a single cell (it's likely computed from the data). Showing ${where}.`
          : "This value isn't a single cell (it's likely computed from the data).";
      return located.page
        ? `Couldn't find this exact text on ${where}. It may be reworded or computed, or the page may be scanned. Showing the cited page.`
        : "Couldn't find this exact text in the document. It may be reworded or computed, or the page may be scanned.";
  }
}

export function HighlightBanner({ doc, highlight, located, onClear }: { doc: Doc; highlight: Highlight; located: Located | null; onClear?: () => void }) {
  const hit = located && located.status !== "not_found";
  return (
    <div className={cn("flex items-start gap-2.5 rounded-lg border px-3 py-2 text-sm", hit ? "border-warn bg-warn-soft" : "border-line bg-panel-2")}>
      <Crosshair className={cn("mt-0.5 h-4 w-4 shrink-0", hit ? "text-warn" : "text-muted")} />
      <div className="min-w-0 flex-1">
        <div className="break-words text-ink">
          <span className="text-ink-2">{highlight.label}:</span> <span className="font-medium">{highlight.text}</span>
        </div>
        <div className="text-xs text-ink-2">{located ? locatedMessage(doc, located) : "Locating in the document…"}</div>
      </div>
      {onClear && (
        <button onClick={onClear} className="rounded p-1 text-muted hover:bg-panel hover:text-ink" aria-label="Clear highlight">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

const PAD = 0.004; // breathing room around highlighted text, in page fractions

/** One rendered PDF page with the located boxes drawn over it; scrolls the first box into view. */
export function PdfPage({ doc, page, boxes, zoom }: { doc: Doc; page: number; boxes: Extract<Located, { kind: "pdf" }>["boxes"]; zoom: number }) {
  const anchor = useRef<HTMLDivElement>(null);
  const scrollToBox = () => anchor.current?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  // the image may already be cached (no onLoad after the boxes arrive), so also scroll when they change
  useEffect(() => {
    anchor.current?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }, [boxes, page, zoom]);
  return (
    <div className="relative mx-auto" style={{ width: `${Math.round(zoom * 100)}%`, maxWidth: "none" }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={api.pageUrl(doc.id, page)} alt={`Page ${page}`} onLoad={scrollToBox} className="block w-full rounded bg-white shadow-md" />
      {boxes.length > 0 && (
        <>
          <svg aria-label="Highlighted value" className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none">
            {boxes.map((b, i) => (
              <rect
                key={i}
                x={b.x - PAD}
                y={b.y - PAD}
                width={b.width + 2 * PAD}
                height={b.height + 2 * PAD}
                rx={0.003}
                fill="rgba(250, 204, 21, 0.38)"
                stroke="#dc2626"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>
          <div
            ref={anchor}
            aria-hidden
            className="pointer-events-none absolute"
            style={{ left: `${boxes[0].x * 100}%`, top: `${boxes[0].y * 100}%`, width: `${boxes[0].width * 100}%`, height: `${boxes[0].height * 100}%` }}
          />
        </>
      )}
    </div>
  );
}

export function SheetView({
  doc,
  idx,
  setIdx,
  cells,
  compact = false,
}: {
  doc: Doc;
  idx: number;
  setIdx: (i: number) => void;
  cells: { row: number; col: number }[];
  compact?: boolean;
}) {
  const [loaded, setLoaded] = useState<{ idx: number; name: string; rows: string[][]; total_rows: number; total_cols: number } | null>(null);
  const grid = loaded?.idx === idx ? loaded : null;
  const marked = new Set(cells.map((c) => `${c.row}:${c.col}`));
  const first = cells[0];
  const firstCell = useRef<HTMLTableCellElement>(null);
  useEffect(() => {
    api
      .sheet(doc.id, idx)
      .then((g) => setLoaded({ idx, ...g }))
      .catch(() => setLoaded(null));
  }, [doc.id, idx]);
  useEffect(() => {
    firstCell.current?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }, [grid, first?.row, first?.col]);
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
      {meta && !compact && (
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
        <div className={cn("overflow-auto rounded-lg border border-line bg-panel", compact ? "max-h-[calc(100dvh-20rem)]" : "max-h-[620px]")}>
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
                  {r.map((v, j) => {
                    const hit = marked.has(`${i}:${j}`);
                    return (
                      <td
                        key={j}
                        title={v}
                        ref={first && first.row === i && first.col === j ? firstCell : undefined}
                        className={cn(
                          "max-w-[260px] truncate whitespace-nowrap border border-line px-2 py-1 text-ink",
                          /^-?[\d.,]+%?$/.test(v) && "text-right tabular-nums",
                          hit && "bg-warn-soft font-semibold outline outline-2 -outline-offset-2 outline-warn",
                        )}
                      >
                        {v}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {grid.total_rows > grid.rows.length && (
            <div className="px-3 py-2 text-xs text-muted">
              Showing the first {grid.rows.length} of {grid.total_rows.toLocaleString()} rows. The Data tab and chat cover all rows.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
