"use client";

import {
  ArrowUp,
  Brain,
  CheckCircle2,
  ChevronDown,
  Database,
  Eye,
  FileText,
  ImageIcon,
  Layers,
  LineChart,
  Search,
  Settings2,
  Sparkles,
  Square,
  Trash2,
  XCircle,
  Zap,
} from "lucide-react";
import { useEffect, useImperativeHandle, useRef, useState, type ReactNode, type Ref } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, streamChat, type ChartSpec, type ChatOptions, type Doc, type Source } from "@/lib/api";
import ChartView from "./ChartView";
import { DataGrid } from "./DataTab";
import { Badge, Button, CopyButton, Spinner, cn } from "./ui";

interface Step {
  id: string;
  name: string;
  label: string;
  input: Record<string, unknown>;
  status: "running" | "ok" | "error";
  detail?: Record<string, unknown>;
}

interface Msg {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  steps?: Step[];
  sources?: Source[];
  charts?: ChartSpec[];
  techniques?: string[];
  cached?: boolean;
  error?: string;
  streaming?: boolean;
}

const TOOL_ICON: Record<string, ReactNode> = {
  search_documents: <Search className="h-3.5 w-3.5" />,
  list_tables: <Layers className="h-3.5 w-3.5" />,
  run_sql: <Database className="h-3.5 w-3.5" />,
  view_pages: <Eye className="h-3.5 w-3.5" />,
  create_chart: <LineChart className="h-3.5 w-3.5" />,
};

const DEFAULT_OPTS: ChatOptions = { effort: "medium", colpali: true, rerank: true, vision: true, adaptive: true, cache: true };

function withCitations(md: string) {
  return md.replace(/\[(S\d+)\]/g, "[$1](#cite-$1)");
}

function StepRow({ step }: { step: Step }) {
  const [open, setOpen] = useState(false);
  const d = step.detail ?? {};
  const trace = d.trace as Record<string, unknown> | undefined;
  return (
    <div className="text-xs">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 py-1 text-left text-ink-2 hover:text-ink">
        {step.status === "running" ? (
          <Spinner className="h-3.5 w-3.5 text-accent" />
        ) : step.status === "ok" ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-good" />
        ) : (
          <XCircle className="h-3.5 w-3.5 text-bad" />
        )}
        <span className="text-muted">{TOOL_ICON[step.name]}</span>
        <span className="min-w-0 flex-1 truncate">{step.label}</span>
        {step.name === "search_documents" && d.hits !== undefined && (
          <span className="text-muted">
            {String(d.hits)} hits{d.images ? ` · ${String(d.images)} pages` : ""}
          </span>
        )}
        {step.name === "run_sql" && d.row_count !== undefined && <span className="text-muted">{String(d.row_count)} rows</span>}
        <ChevronDown className={cn("h-3 w-3 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mb-1.5 ml-6 space-y-2 rounded-lg bg-panel-2 p-2.5">
          {typeof step.input.sql === "string" && <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[11.5px] text-ink">{step.input.sql}</pre>}
          {Array.isArray(step.input.queries) && (
            <div className="space-y-0.5">
              {(step.input.queries as string[]).map((q) => (
                <div key={q} className="text-ink">
                  → {q}
                </div>
              ))}
              {typeof step.input.hypothetical_answer === "string" && (
                <div className="mt-1 text-muted">
                  <span className="font-medium">HyDE:</span> {step.input.hypothetical_answer}
                </div>
              )}
            </div>
          )}
          {trace && (
            <div className="flex flex-wrap gap-1">
              {(trace.signals_used as string[] | undefined)?.map((s) => (
                <Badge key={s}>{s}</Badge>
              ))}
              {trace.reranked ? <Badge>reranked</Badge> : null}
              {Array.isArray(trace.colpali_pages) && trace.colpali_pages.length > 0 && (
                <Badge tone="good">ColPali: p.{(trace.colpali_pages as { page: number }[]).map((p) => p.page).join(", ")}</Badge>
              )}
            </div>
          )}
          {Array.isArray(d.columns) && Array.isArray(d.rows) && (d.rows as unknown[][]).length > 0 && (
            <DataGrid columns={d.columns as string[]} rows={d.rows as unknown[][]} height={220} />
          )}
          {typeof d.error === "string" && <div className="text-bad">{d.error}</div>}
        </div>
      )}
    </div>
  );
}

function SourceModal({ source, onClose }: { source: Source; onClose: () => void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose} role="dialog" aria-modal>
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-line bg-panel shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Badge tone="accent">{source.id}</Badge>
              <span className="truncate font-medium text-ink">{source.filename}</span>
            </div>
            <div className="mt-0.5 text-xs text-muted">
              {source.page ? `Page ${source.page}` : source.sheet ? `Sheet ${source.sheet}` : source.kind === "sql" ? "SQL query result" : ""}
              {source.section ? ` · ${source.section}` : ""}
            </div>
          </div>
          <Button size="sm" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
        <div className="space-y-4 overflow-y-auto p-5">
          {source.kind === "sql" ? (
            <>
              <pre className="whitespace-pre-wrap rounded-lg bg-panel-2 p-3 font-mono text-xs text-ink">{source.sql}</pre>
              {source.columns && source.rows && <DataGrid columns={source.columns} rows={source.rows} height={360} />}
            </>
          ) : (
            source.snippet &&
            source.kind !== "page" && (
              <blockquote className="whitespace-pre-wrap rounded-lg border-l-4 border-accent bg-accent-soft p-3 text-sm text-ink">{source.snippet}</blockquote>
            )
          )}
          {source.page && (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img src={api.pageUrl(source.doc_id, source.page)} alt={`Page ${source.page}`} className="mx-auto w-full rounded border border-line bg-white" />
          )}
        </div>
      </div>
    </div>
  );
}

function AssistantMessage({ msg, onCite }: { msg: Msg; onCite: (s: Source) => void }) {
  const [showThinking, setShowThinking] = useState(false);
  const [showSources, setShowSources] = useState(false);
  const byId = new Map((msg.sources ?? []).map((s) => [s.id, s]));
  const cited = (msg.sources ?? []).filter((s) => msg.content.includes(`[${s.id}]`));
  const running = msg.steps?.some((s) => s.status === "running");

  return (
    <div className="space-y-2">
      {!!msg.steps?.length && (
        <div className="rounded-xl border border-line bg-panel px-3 py-1.5">
          {msg.steps.map((s) => (
            <StepRow key={s.id} step={s} />
          ))}
        </div>
      )}
      {msg.thinking && (
        <div>
          <button onClick={() => setShowThinking((v) => !v)} className="flex items-center gap-1.5 text-xs text-muted hover:text-ink">
            <Brain className="h-3.5 w-3.5" /> Reasoning <ChevronDown className={cn("h-3 w-3", showThinking && "rotate-180")} />
          </button>
          {showThinking && <div className="mt-1.5 whitespace-pre-wrap rounded-lg bg-panel-2 p-3 text-xs leading-relaxed text-ink-2">{msg.thinking}</div>}
        </div>
      )}

      {msg.content ? (
        <div className="prose-answer">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => {
                if (href?.startsWith("#cite-")) {
                  const id = href.slice(6);
                  const s = byId.get(id);
                  return (
                    <button
                      onClick={() => s && onCite(s)}
                      title={s ? `${s.filename ?? ""} ${s.page ? `p.${s.page}` : s.sheet ?? ""}` : id}
                      className="mx-0.5 inline-flex h-[18px] -translate-y-px items-center rounded bg-accent-soft px-1 align-middle text-[10.5px] font-semibold text-accent-ink hover:bg-accent hover:text-white"
                    >
                      {s?.page ? `p.${s.page}` : s?.kind === "sql" ? "SQL" : id}
                    </button>
                  );
                }
                return (
                  <a href={href} target="_blank" rel="noreferrer" className="text-accent underline">
                    {children}
                  </a>
                );
              },
            }}
          >
            {withCitations(msg.content)}
          </ReactMarkdown>
        </div>
      ) : (
        msg.streaming &&
        !running && (
          <div className="flex gap-1 py-2">
            <span className="typing-dot h-2 w-2 rounded-full bg-accent" />
            <span className="typing-dot h-2 w-2 rounded-full bg-accent" />
            <span className="typing-dot h-2 w-2 rounded-full bg-accent" />
          </div>
        )
      )}

      {msg.charts?.map((c, i) => (
        <ChartView key={i} spec={c} />
      ))}

      {msg.error && <div className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">{msg.error}</div>}

      {!msg.streaming && (msg.content || msg.sources?.length) && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {cited.length > 0 && (
              <button
                onClick={() => setShowSources((v) => !v)}
                className="flex items-center gap-1 rounded-md border border-line px-2 py-0.5 text-[11px] font-medium text-ink-2 hover:bg-panel-2"
              >
                <FileText className="h-3 w-3" /> {cited.length} source{cited.length === 1 ? "" : "s"}
                <ChevronDown className={cn("h-3 w-3", showSources && "rotate-180")} />
              </button>
            )}
            {msg.cached && (
              <Badge tone="good">
                <Zap className="h-3 w-3" /> cached
              </Badge>
            )}
            <CopyButton text={msg.content} />
          </div>
          {showSources && (
            <div className="grid gap-1.5">
              {cited.map((s) => (
                <button key={s.id} onClick={() => onCite(s)} className="flex items-start gap-2 rounded-lg border border-line p-2 text-left hover:border-accent">
                  <Badge tone="accent">{s.id}</Badge>
                  {s.kind === "page" ? (
                    <ImageIcon className="mt-0.5 h-3.5 w-3.5 text-muted" />
                  ) : s.kind === "sql" ? (
                    <Database className="mt-0.5 h-3.5 w-3.5 text-muted" />
                  ) : (
                    <FileText className="mt-0.5 h-3.5 w-3.5 text-muted" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-ink">
                      {s.filename} {s.page ? `· p.${s.page}` : s.sheet ? `· ${s.sheet}` : ""}
                    </div>
                    <div className="line-clamp-2 text-[11px] text-muted">{s.kind === "sql" ? s.sql : s.snippet}</div>
                  </div>
                </button>
              ))}
            </div>
          )}
          {!!msg.techniques?.length && (
            <div className="flex flex-wrap gap-1">
              {msg.techniques.map((t) => (
                <span key={t} className="rounded bg-panel-2 px-1.5 py-0.5 text-[10px] text-muted">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export interface ChatHandle {
  ask: (question: string, extraDocIds?: string[]) => void;
}

export default function ChatPanel({
  ref,
  docs,
  scope,
  llmAvailable,
  colpaliAvailable,
}: {
  ref?: Ref<ChatHandle>;
  docs: Doc[];
  scope: string[];
  llmAvailable: boolean;
  colpaliAvailable: boolean;
}) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [opts, setOpts] = useState<ChatOptions>(DEFAULT_OPTS);
  const [showOpts, setShowOpts] = useState(false);
  const [modal, setModal] = useState<Source | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const scopedDocs = docs.filter((d) => scope.includes(d.id));
  const suggestions = [...new Set(scopedDocs.flatMap((d) => d.extraction?.suggested_questions?.slice(0, 3) ?? []))].slice(0, 5);

  const patch = (id: string, fn: (m: Msg) => Msg) => setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m)));

  const send = async (question: string, extraDocIds: string[] = []) => {
    if (!question.trim() || busy) return;
    const docIds = [...new Set([...scope, ...extraDocIds])];
    const history = messages.filter((m) => m.content && !m.error).map((m) => ({ role: m.role, content: m.content }));
    const uid = crypto.randomUUID();
    const aid = crypto.randomUUID();
    setMessages((ms) => [...ms, { id: uid, role: "user", content: question }, { id: aid, role: "assistant", content: "", streaming: true, steps: [], sources: [], charts: [] }]);
    setInput("");
    setBusy(true);
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      for await (const ev of streamChat({ question, history, doc_ids: docIds, options: opts }, ctrl.signal)) {
        switch (ev.type) {
          case "text":
            patch(aid, (m) => ({ ...m, content: m.content + ev.delta }));
            break;
          case "thinking":
            patch(aid, (m) => ({ ...m, thinking: (m.thinking ?? "") + ev.delta }));
            break;
          case "sources":
            patch(aid, (m) => ({ ...m, sources: [...(m.sources ?? []), ...ev.sources] }));
            break;
          case "chart":
            patch(aid, (m) => ({ ...m, charts: [...(m.charts ?? []), ev.chart] }));
            break;
          case "tool_start":
            patch(aid, (m) => ({ ...m, steps: [...(m.steps ?? []), { id: ev.id, name: ev.name, label: ev.label, input: ev.input, status: "running" }] }));
            break;
          case "tool_end":
            patch(aid, (m) => ({ ...m, steps: (m.steps ?? []).map((s) => (s.id === ev.id ? { ...s, status: ev.ok ? "ok" : "error", detail: ev.detail } : s)) }));
            break;
          case "done":
            patch(aid, (m) => ({ ...m, streaming: false, cached: ev.cached, techniques: ev.techniques }));
            break;
          case "error":
            patch(aid, (m) => ({ ...m, error: ev.message }));
            break;
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") patch(aid, (m) => ({ ...m, error: `Connection error: ${(e as Error).message}` }));
    } finally {
      patch(aid, (m) => ({ ...m, streaming: false, steps: (m.steps ?? []).map((s) => (s.status === "running" ? { ...s, status: "error" } : s)) }));
      setBusy(false);
      abort.current = null;
    }
  };

  useImperativeHandle(ref, () => ({ ask: (q, extra) => void send(q, extra) }));

  const toggle = (k: keyof ChatOptions) => setOpts((o) => ({ ...o, [k]: !o[k] }));

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-sm font-semibold text-ink">
            <Sparkles className="h-4 w-4 text-accent" /> Ask DocuSight
          </div>
          <div className="truncate text-[11px] text-muted">
            {scopedDocs.length ? `Across ${scopedDocs.length} document${scopedDocs.length === 1 ? "" : "s"}: ${scopedDocs.map((d) => d.filename).join(", ")}` : "Select documents in the sidebar"}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={() => setShowOpts((v) => !v)} aria-label="Retrieval settings" className={cn(showOpts && "bg-panel-2")}>
            <Settings2 className="h-4 w-4" />
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setMessages([])} aria-label="Clear chat" disabled={busy || !messages.length}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {showOpts && (
        <div className="space-y-2.5 border-b border-line bg-panel-2 px-4 py-3 text-xs">
          <div className="flex items-center gap-2">
            <span className="w-24 font-medium text-ink-2">Reasoning effort</span>
            {(["low", "medium", "high"] as const).map((e) => (
              <button
                key={e}
                onClick={() => setOpts((o) => ({ ...o, effort: e }))}
                className={cn("rounded-md px-2 py-1 font-medium capitalize", opts.effort === e ? "bg-accent text-white" : "bg-panel text-ink-2 hover:text-ink")}
              >
                {e}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
            {(
              [
                ["colpali", `ColPali visual retrieval${colpaliAvailable ? "" : " (not installed)"}`],
                ["rerank", "Cross-encoder rerank"],
                ["vision", "Send page images to Claude"],
                ["adaptive", "Full-context for small docs"],
                ["cache", "Semantic answer cache"],
              ] as const
            ).map(([k, label]) => (
              <label key={k} className="flex items-center gap-1.5 text-ink-2">
                <input type="checkbox" checked={opts[k] as boolean} onChange={() => toggle(k)} className="accent-[var(--accent)]" disabled={k === "colpali" && !colpaliAvailable} />
                {label}
              </label>
            ))}
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
        {!messages.length && (
          <div className="pt-6 text-center">
            <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-2xl bg-accent-soft">
              <Sparkles className="h-5 w-5 text-accent" />
            </div>
            <p className="font-semibold text-ink">Ask anything about your documents</p>
            <p className="mx-auto mt-1 max-w-xs text-sm text-ink-2">Answers cite their sources. Numbers from spreadsheets are computed with SQL, and charts are read visually.</p>
            {!llmAvailable && (
              <div className="mx-auto mt-4 max-w-xs rounded-lg bg-warn-soft px-3 py-2 text-left text-xs text-warn">
                No Anthropic API key detected. Set <code>ANTHROPIC_API_KEY</code> in <code>.env</code> and restart the backend to enable chat.
              </div>
            )}
            <div className="mt-5 flex flex-col items-stretch gap-2">
              {(suggestions.length ? suggestions : ["Summarize the key points", "What are the most important numbers?", "Are there any risks or inconsistencies?"]).map((q) => (
                <button
                  key={q}
                  disabled={!scope.length}
                  onClick={() => send(q)}
                  className="rounded-xl border border-line bg-panel px-3.5 py-2 text-left text-sm text-ink hover:border-accent hover:bg-accent-soft disabled:opacity-50"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent px-3.5 py-2 text-sm text-white">{m.content}</div>
            </div>
          ) : (
            <AssistantMessage key={m.id} msg={m} onCite={setModal} />
          ),
        )}
        <div ref={bottom} />
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="border-t border-line p-3"
      >
        <div className="flex items-end gap-2 rounded-2xl border border-line bg-panel px-3 py-2 focus-within:border-accent">
          <textarea
            ref={textarea}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            rows={1}
            placeholder={scope.length ? "Ask a question, request a chart, compare documents…" : "Upload or select a document first"}
            disabled={!scope.length}
            className="max-h-40 min-h-[24px] flex-1 resize-none bg-transparent py-1 text-sm text-ink outline-none placeholder:text-muted"
            aria-label="Question"
          />
          {busy ? (
            <button type="button" onClick={() => abort.current?.abort()} className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-panel" aria-label="Stop">
              <Square className="h-3 w-3 fill-current" />
            </button>
          ) : (
            <button type="submit" disabled={!input.trim() || !scope.length} className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-white disabled:opacity-40" aria-label="Send">
              <ArrowUp className="h-4 w-4" />
            </button>
          )}
        </div>
        <div className="mt-1.5 text-center text-[10.5px] text-muted">Enter to send · Shift+Enter for a new line</div>
      </form>

      {modal && <SourceModal source={modal} onClose={() => setModal(null)} />}
    </div>
  );
}
