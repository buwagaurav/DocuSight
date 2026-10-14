"use client";

import {
  AlertTriangle,
  Building2,
  CalendarDays,
  Download,
  FileSearch,
  Gauge,
  Lightbulb,
  ListTree,
  MessageCircleQuestion,
  RefreshCw,
  Search,
  Sparkles,
  Star,
} from "lucide-react";
import { useMemo, useState } from "react";
import { api, type Doc, type KeyField } from "@/lib/api";
import { Badge, Button, Card, CopyButton, SectionTitle, Spinner, cn, download, toCSV } from "./ui";

const IMPORTANCE_ORDER = { critical: 0, high: 1, normal: 2 } as const;

function SourceChip({ source, onOpen }: { source: string; onOpen: (page: number) => void }) {
  const m = source.match(/p(?:age)?\.?\s*(\d+)/i);
  if (m) {
    return (
      <button onClick={() => onOpen(Number(m[1]))} className="text-[11px] text-accent hover:underline" title="Open page">
        {source}
      </button>
    );
  }
  return <span className="text-[11px] text-muted">{source}</span>;
}

export default function OverviewTab({ doc, onAsk, onOpenPage }: { doc: Doc; onAsk: (q: string) => void; onOpenPage: (p: number) => void }) {
  const ex = doc.extraction;
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [criticalOnly, setCriticalOnly] = useState(false);

  const categories = useMemo(() => [...new Set((ex?.key_fields ?? []).map((f) => f.category).filter(Boolean))], [ex]);
  const fields = useMemo(() => {
    const q = query.toLowerCase();
    return [...(ex?.key_fields ?? [])]
      .filter((f) => (!category || f.category === category) && (!criticalOnly || f.importance !== "normal"))
      .filter((f) => !q || `${f.label} ${f.value}`.toLowerCase().includes(q))
      .sort((a, b) => (IMPORTANCE_ORDER[a.importance] ?? 2) - (IMPORTANCE_ORDER[b.importance] ?? 2));
  }, [ex, query, category, criticalOnly]);

  if (!ex) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
        <Spinner className="h-7 w-7 text-accent" />
        <p className="font-medium text-ink">Reading the document…</p>
        <p className="max-w-sm text-sm text-ink-2">
          Claude is looking at the pages and tables to extract every key field, metric and insight. You can already search or chat.
        </p>
      </div>
    );
  }

  const exportFields = (fmt: "csv" | "json") => {
    const base = doc.filename.replace(/\.[^.]+$/, "");
    if (fmt === "json") download(`${base}-extraction.json`, JSON.stringify(ex, null, 2), "application/json");
    else
      download(
        `${base}-fields.csv`,
        toCSV(["label", "value", "category", "importance", "source"], ex.key_fields.map((f: KeyField) => [f.label, f.value, f.category, f.importance, f.source])),
        "text/csv",
      );
  };

  return (
    <div className="space-y-5">
      {/* header */}
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
              <Badge tone="accent">{ex.document_type || "Document"}</Badge>
              {ex.language && <Badge>{ex.language}</Badge>}
              {doc.extraction_status === "running" && (
                <Badge tone="accent">
                  <Spinner className="h-3 w-3" /> refreshing
                </Badge>
              )}
              {ex._engine === "heuristic" && <Badge tone="warn">basic extraction (no LLM)</Badge>}
            </div>
            <h2 className="text-xl font-semibold text-ink">{ex.title || doc.filename}</h2>
          </div>
          <div className="flex gap-1.5">
            <Button size="sm" onClick={() => exportFields("csv")}>
              <Download className="h-3.5 w-3.5" /> CSV
            </Button>
            <Button size="sm" onClick={() => exportFields("json")}>
              <Download className="h-3.5 w-3.5" /> JSON
            </Button>
            <Button size="sm" variant="ghost" title="Re-run extraction" onClick={() => api.reExtract(doc.id)}>
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        <p className="mt-3 text-[15px] leading-relaxed text-ink-2">{ex.summary}</p>
        {ex._error && (
          <div className="mt-3 flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {ex._error}
          </div>
        )}
      </Card>

      {/* metrics */}
      {ex.metrics.length > 0 && (
        <section>
          <SectionTitle icon={<Gauge className="h-4 w-4" />}>Key metrics</SectionTitle>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
            {ex.metrics.slice(0, 12).map((m, i) => (
              <Card key={i} className="p-3.5">
                <div className="text-xs font-medium text-ink-2">{m.label}</div>
                <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-ink">
                  {m.value}
                  {m.unit && !m.value.includes(m.unit) && <span className="ml-1 text-sm font-medium text-muted">{m.unit}</span>}
                </div>
                {m.context && <div className="mt-1 line-clamp-2 text-xs text-muted">{m.context}</div>}
                <div className="mt-1.5">
                  <SourceChip source={m.source} onOpen={onOpenPage} />
                </div>
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* key fields */}
      <section>
        <SectionTitle
          icon={<FileSearch className="h-4 w-4" />}
          right={<span className="text-xs text-muted">{fields.length} of {ex.key_fields.length}</span>}
        >
          Extracted fields
        </SectionTitle>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-muted" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter fields…"
              className="h-8 w-52 rounded-lg border border-line bg-panel pl-8 pr-2 text-sm text-ink outline-none focus:border-accent"
            />
          </div>
          <button
            onClick={() => setCriticalOnly((v) => !v)}
            className={cn(
              "flex h-8 items-center gap-1 rounded-lg border px-2.5 text-xs font-medium",
              criticalOnly ? "border-accent bg-accent-soft text-accent-ink" : "border-line text-ink-2 hover:bg-panel-2",
            )}
          >
            <Star className="h-3.5 w-3.5" /> Mandatory only
          </button>
          {categories.map((c) => (
            <button
              key={c}
              onClick={() => setCategory(category === c ? null : c)}
              className={cn(
                "h-8 rounded-lg border px-2.5 text-xs font-medium",
                category === c ? "border-accent bg-accent-soft text-accent-ink" : "border-line text-ink-2 hover:bg-panel-2",
              )}
            >
              {c}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-2.5">
          {fields.map((f, i) => (
            <Card key={i} className={cn("group p-3", f.importance === "critical" && "border-l-[3px] border-l-accent")}>
              <div className="flex items-start justify-between gap-2">
                <div className="text-xs font-medium text-ink-2">{f.label}</div>
                <div className="flex items-center gap-1">
                  {f.importance === "critical" && <Badge tone="accent">mandatory</Badge>}
                  {f.importance === "high" && <Badge>important</Badge>}
                  <CopyButton text={f.value} className="opacity-0 group-hover:opacity-100" />
                </div>
              </div>
              <div className="mt-1 break-words text-[15px] font-medium text-ink">{f.value}</div>
              <div className="mt-1.5 flex items-center justify-between">
                <span className="text-[11px] text-muted">{f.category}</span>
                <SourceChip source={f.source} onOpen={onOpenPage} />
              </div>
            </Card>
          ))}
          {fields.length === 0 && <p className="col-span-full py-6 text-center text-sm text-muted">No fields match.</p>}
        </div>
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        {ex.insights.length > 0 && (
          <section>
            <SectionTitle icon={<Lightbulb className="h-4 w-4" />}>Insights</SectionTitle>
            <Card className="divide-y divide-[var(--border)]">
              {ex.insights.map((t, i) => (
                <div key={i} className="flex gap-2.5 px-4 py-3 text-sm text-ink">
                  <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-accent" /> {t}
                </div>
              ))}
            </Card>
          </section>
        )}
        {ex.anomalies.length > 0 && (
          <section>
            <SectionTitle icon={<AlertTriangle className="h-4 w-4" />}>Anomalies & risks</SectionTitle>
            <Card className="divide-y divide-[var(--border)]">
              {ex.anomalies.map((t, i) => (
                <div key={i} className="flex gap-2.5 px-4 py-3 text-sm text-ink">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" /> {t}
                </div>
              ))}
            </Card>
          </section>
        )}
        {ex.entities.length > 0 && (
          <section>
            <SectionTitle icon={<Building2 className="h-4 w-4" />}>Entities</SectionTitle>
            <Card className="flex flex-wrap gap-2 p-3.5">
              {ex.entities.map((e, i) => (
                <span key={i} className="rounded-lg border border-line px-2.5 py-1.5 text-sm" title={e.role}>
                  <span className="font-medium text-ink">{e.name}</span>
                  <span className="ml-1.5 text-xs text-muted">{e.role || e.type}</span>
                </span>
              ))}
            </Card>
          </section>
        )}
        {ex.dates.length > 0 && (
          <section>
            <SectionTitle icon={<CalendarDays className="h-4 w-4" />}>Dates</SectionTitle>
            <Card className="divide-y divide-[var(--border)]">
              {ex.dates.map((d, i) => (
                <div key={i} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                  <span className="text-ink-2">{d.label}</span>
                  <span className="flex items-center gap-2">
                    <span className="font-medium tabular-nums text-ink">{d.date}</span>
                    <SourceChip source={d.source} onOpen={onOpenPage} />
                  </span>
                </div>
              ))}
            </Card>
          </section>
        )}
      </div>

      {ex.sections.length > 0 && (
        <section>
          <SectionTitle icon={<ListTree className="h-4 w-4" />}>Structure</SectionTitle>
          <Card className="divide-y divide-[var(--border)]">
            {ex.sections.map((s, i) => (
              <div key={i} className="flex items-start justify-between gap-3 px-4 py-2.5 text-sm">
                <div>
                  <div className="font-medium text-ink">{s.title}</div>
                  <div className="text-ink-2">{s.description}</div>
                </div>
                <SourceChip source={s.source} onOpen={onOpenPage} />
              </div>
            ))}
          </Card>
        </section>
      )}

      {ex.suggested_questions.length > 0 && (
        <section>
          <SectionTitle icon={<MessageCircleQuestion className="h-4 w-4" />}>Ask about this document</SectionTitle>
          <div className="flex flex-wrap gap-2">
            {ex.suggested_questions.map((q, i) => (
              <button
                key={i}
                onClick={() => onAsk(q)}
                className="rounded-full border border-line bg-panel px-3.5 py-1.5 text-left text-sm text-ink hover:border-accent hover:bg-accent-soft"
              >
                {q}
              </button>
            ))}
          </div>
        </section>
      )}

      {doc.techniques.length > 0 && (
        <section>
          <SectionTitle>Processing pipeline</SectionTitle>
          <div className="flex flex-wrap gap-1.5">
            {doc.techniques.map((t) => (
              <Badge key={t}>{t}</Badge>
            ))}
            {ex._engine && ex._engine !== "heuristic" && <Badge tone="accent">Extraction: {ex._engine} (vision + structured output)</Badge>}
          </div>
        </section>
      )}
    </div>
  );
}
