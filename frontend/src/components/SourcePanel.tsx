"use client";

import { ChevronLeft, ChevronRight, Crosshair, Maximize2, X, ZoomIn, ZoomOut } from "lucide-react";
import { useState } from "react";
import type { Doc, Highlight } from "@/lib/api";
import { HighlightBanner, PdfPage, SheetView, useLocated } from "./SourceView";
import { Button, Spinner } from "./ui";

/** Right-hand panel beside the Overview: the clicked value, highlighted in its page or sheet. */
export default function SourcePanel({
  doc,
  highlight,
  onClose,
  onOpenFull,
}: {
  doc: Doc;
  highlight: Highlight;
  onClose: () => void;
  onOpenFull: () => void;
}) {
  // zoomed in by default: the panel is narrow, and the point is to read the highlighted text
  const [zoom, setZoom] = useState(1.6);
  // manual page/sheet navigation, remembered only for the value it was made on
  const [nav, setNav] = useState<{ for: Highlight; page?: number; sheet?: number } | null>(null);
  const located = useLocated(doc.id, highlight);
  const own = nav?.for === highlight ? nav : null;

  const page = own?.page ?? (located?.kind === "pdf" ? located.page : null) ?? null;
  const sheet = own?.sheet ?? (located?.kind === "sheet" ? located.sheet_index : null) ?? 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-sm font-semibold text-ink">
            <Crosshair className="h-4 w-4 text-accent" /> Source
          </div>
          <div className="truncate text-[11px] text-muted">{doc.filename}</div>
        </div>
        <div className="flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={onOpenFull} aria-label="Open in the Pages tab" title={doc.kind === "pdf" ? "Open in Pages tab" : "Open in Sheets tab"}>
            <Maximize2 className="h-4 w-4" />
          </Button>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close source panel" title="Close (back to chat)">
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-3 p-3">
        <HighlightBanner doc={doc} highlight={highlight} located={located} />

        {!located ? (
          <div className="flex justify-center py-16">
            <Spinner className="h-6 w-6 text-accent" />
          </div>
        ) : doc.kind !== "pdf" ? (
          <SheetView
            compact
            doc={doc}
            idx={sheet}
            setIdx={(i) => setNav({ for: highlight, sheet: i })}
            cells={located.kind === "sheet" && located.sheet_index === sheet ? located.cells : []}
          />
        ) : page ? (
          <>
            <div className="flex items-center gap-1">
              <Button size="sm" disabled={page <= 1} onClick={() => setNav({ for: highlight, page: page - 1 })} aria-label="Previous page">
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="text-xs tabular-nums text-ink-2">
                Page {page} / {doc.n_pages}
              </span>
              <Button size="sm" disabled={page >= doc.n_pages} onClick={() => setNav({ for: highlight, page: page + 1 })} aria-label="Next page">
                <ChevronRight className="h-4 w-4" />
              </Button>
              <div className="ml-auto flex items-center gap-1">
                <Button size="sm" variant="ghost" onClick={() => setZoom(Math.max(0.5, zoom - 0.25))} aria-label="Zoom out">
                  <ZoomOut className="h-4 w-4" />
                </Button>
                <span className="w-10 text-center text-xs tabular-nums text-ink-2">{Math.round(zoom * 100)}%</span>
                <Button size="sm" variant="ghost" onClick={() => setZoom(Math.min(3, zoom + 0.25))} aria-label="Zoom in">
                  <ZoomIn className="h-4 w-4" />
                </Button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-line bg-panel-2 p-2">
              <PdfPage doc={doc} page={page} zoom={zoom} boxes={located.kind === "pdf" && located.page === page ? located.boxes : []} />
            </div>
          </>
        ) : (
          <p className="py-6 text-center text-sm text-muted">No page to show for this value.</p>
        )}
      </div>
    </div>
  );
}
