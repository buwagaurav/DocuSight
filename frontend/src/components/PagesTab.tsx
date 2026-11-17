"use client";

import { ChevronLeft, ChevronRight, Download, ZoomIn, ZoomOut } from "lucide-react";
import { useState } from "react";
import { api, type Doc, type Highlight } from "@/lib/api";
import { HighlightBanner, PdfPage, SheetView, useLocated } from "./SourceView";
import { Button, Card } from "./ui";

export default function PagesTab({
  doc,
  page,
  setPage,
  highlight,
  onClearHighlight,
}: {
  doc: Doc;
  page: number | null;
  setPage: (p: number | null) => void;
  highlight: Highlight | null;
  onClearHighlight: () => void;
}) {
  const [zoom, setZoom] = useState(1);
  const [sheetIdx, setSheetIdx] = useState(0);
  const located = useLocated(doc.id, highlight, (r) => {
    if (r.kind === "pdf" && r.page) setPage(r.page);
    if (r.kind === "sheet" && r.sheet_index != null) setSheetIdx(r.sheet_index);
  });

  const banner = highlight && <HighlightBanner doc={doc} highlight={highlight} located={located} onClear={onClearHighlight} />;

  if (doc.kind !== "pdf") {
    return (
      <div className="space-y-3">
        {banner}
        <SheetView doc={doc} idx={sheetIdx} setIdx={setSheetIdx} cells={located?.kind === "sheet" && located.sheet_index === sheetIdx ? located.cells : []} />
      </div>
    );
  }

  if (page) {
    return (
      <div className="space-y-3">
        {banner}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={() => {
              setPage(null);
              onClearHighlight();
            }}
          >
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
        <div className="max-h-[75vh] overflow-auto rounded-xl border border-line bg-panel-2 p-4">
          <PdfPage doc={doc} page={page} zoom={zoom} boxes={located?.kind === "pdf" && located.page === page ? located.boxes : []} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {banner}
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
    </div>
  );
}
