"use client";

import { FlaskConical, Search } from "lucide-react";
import { useState } from "react";
import { api, type Doc } from "@/lib/api";
import { Badge, Button, Card, Empty, Spinner } from "./ui";

type Result = Awaited<ReturnType<typeof api.search>>;

const SIGNAL_LABEL: Record<string, string> = { bm25: "BM25", dense: "Dense", colpali: "ColPali", rerank: "Reranked" };

export default function SearchLab({ docs, colpaliAvailable, onOpenPage }: { docs: Doc[]; colpaliAvailable: boolean; onOpenPage: (docId: string, p: number) => void }) {
  const [q, setQ] = useState("");
  const [colpali, setColpali] = useState(true);
  const [rerank, setRerank] = useState(true);
  const [res, setRes] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    if (!q.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      setRes(await api.search(q, docs.map((d) => d.id), colpali, rerank));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <p className="mb-3 text-sm text-ink-2">
          See exactly what the retriever finds before the LLM answers: lexical BM25 + dense embeddings + ColPali visual page matching, fused with
          Reciprocal Rank Fusion and reranked by a cross-encoder.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run();
          }}
          className="flex flex-wrap items-center gap-2"
        >
          <div className="relative min-w-[240px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="e.g. total amount due, revenue by region, signature on last page…"
              className="h-9 w-full rounded-lg border border-line bg-panel pl-9 pr-3 text-sm text-ink outline-none focus:border-accent"
            />
          </div>
          <label className="flex items-center gap-1.5 text-sm text-ink-2">
            <input type="checkbox" checked={colpali} disabled={!colpaliAvailable} onChange={(e) => setColpali(e.target.checked)} className="accent-[var(--accent)]" />
            ColPali{!colpaliAvailable && " (off)"}
          </label>
          <label className="flex items-center gap-1.5 text-sm text-ink-2">
            <input type="checkbox" checked={rerank} onChange={(e) => setRerank(e.target.checked)} className="accent-[var(--accent)]" />
            Rerank
          </label>
          <Button variant="primary" type="submit" disabled={busy}>
            {busy ? <Spinner /> : <FlaskConical className="h-4 w-4" />} Retrieve
          </Button>
        </form>
      </Card>

      {err && <div className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">{err}</div>}
      {!res && !busy && <Empty icon={<FlaskConical className="h-8 w-8" />} title="Retrieval lab">Search across {docs.length} ready document(s).</Empty>}

      {res && (
        <>
          <div className="flex flex-wrap gap-1.5 text-xs">
            {(res.trace.signals_used as string[] | undefined)?.map((s) => (
              <Badge key={s} tone="accent">
                {SIGNAL_LABEL[s] ?? s}
              </Badge>
            ))}
            {res.trace.reranked ? <Badge tone="accent">Cross-encoder rerank</Badge> : null}
            <Badge>RRF fusion</Badge>
            <Badge>{String(res.trace.candidates)} chunks searched</Badge>
          </div>

          {res.pages.length > 0 && (
            <div>
              <div className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-ink-2">Pages sent to vision</div>
              <div className="flex gap-3 overflow-x-auto pb-2">
                {res.pages.map((p) => (
                  <button key={`${p.doc_id}-${p.page}`} onClick={() => onOpenPage(p.doc_id, p.page)} className="w-32 shrink-0 text-left">
                    <Card className="overflow-hidden p-1 hover:border-accent">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={api.pageUrl(p.doc_id, p.page)} alt={`Page ${p.page}`} className="aspect-[3/4] w-full rounded bg-white object-contain object-top" />
                    </Card>
                    <div className="mt-1 truncate text-[11px] text-ink-2">
                      {p.filename} · p.{p.page}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-2">
            {res.hits.map((h, i) => (
              <Card key={h.id} className="p-3.5">
                <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-semibold text-ink">#{i + 1}</span>
                  <span className="text-xs text-ink-2">
                    {h.filename} {h.page ? `· p.${h.page}` : h.sheet ? `· ${h.sheet}` : ""} {h.section ? `· ${h.section}` : ""}
                  </span>
                  <span className="ml-auto text-[11px] tabular-nums text-muted">score {h.score.toFixed(3)}</span>
                </div>
                <div className="mb-2 flex flex-wrap gap-1">
                  {Object.entries(h.signals).map(([k, rank]) => (
                    <Badge key={k} tone={k === "colpali" ? "good" : k === "rerank" ? "accent" : "neutral"}>
                      {SIGNAL_LABEL[k] ?? k}
                      {k !== "rerank" && ` #${rank}`}
                    </Badge>
                  ))}
                  <Badge>{h.kind}</Badge>
                </div>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-ink-2">{h.text}</pre>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
