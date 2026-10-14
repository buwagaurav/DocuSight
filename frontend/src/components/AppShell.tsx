"use client";

import { AlertCircle, Database, Eye, FileStack, FlaskConical, LayoutDashboard, MessageSquare, Moon, ScanEye, Sun, UploadCloud } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Doc, type Health } from "@/lib/api";
import ChatPanel, { type ChatHandle } from "./ChatPanel";
import DataTab from "./DataTab";
import OverviewTab from "./OverviewTab";
import PagesTab from "./PagesTab";
import SearchLab from "./SearchLab";
import Sidebar from "./Sidebar";
import { Badge, Empty, Progress, Spinner, cn } from "./ui";

type Tab = "overview" | "data" | "pages" | "lab";
type MobilePane = "docs" | "doc" | "chat";

export default function AppShell() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [scope, setScope] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState<Tab>("overview");
  const [page, setPage] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [pane, setPane] = useState<MobilePane>("doc");
  // theme only drives <html data-theme>, never rendered markup, so reading storage lazily can't cause a hydration mismatch
  const [theme, setTheme] = useState<"light" | "dark" | null>(() => {
    try {
      const saved = typeof window === "undefined" ? null : localStorage.getItem("docusight-theme");
      return saved === "light" || saved === "dark" ? saved : null;
    } catch {
      return null;
    }
  });
  const knownReady = useRef<Set<string>>(new Set());
  const chat = useRef<ChatHandle>(null);

  const refresh = useCallback(
    () =>
      api
        .docs()
        .then((list) => {
          setDocs(list);
          setBackendDown(false);
          // auto-add newly ready docs to the chat scope
          const newlyReady = list.filter((d) => d.status === "ready" && !knownReady.current.has(d.id));
          if (newlyReady.length) {
            newlyReady.forEach((d) => knownReady.current.add(d.id));
            setScope((s) => new Set([...s, ...newlyReady.map((d) => d.id)]));
          }
          setActiveId((cur) => (cur && list.some((d) => d.id === cur) ? cur : (list[0]?.id ?? null)));
        })
        .catch(() => setBackendDown(true)),
    [],
  );

  useEffect(() => {
    api.health().then(setHealth).catch(() => setBackendDown(true));
    refresh();
  }, [refresh]);

  const busy = docs.some(
    (d) => d.status === "processing" || ["pending", "running"].includes(d.extraction_status) || ["pending", "building"].includes(d.visual_index),
  );
  useEffect(() => {
    const t = setInterval(refresh, busy ? 1500 : 10000);
    return () => clearInterval(t);
  }, [busy, refresh]);

  useEffect(() => {
    if (theme) {
      document.documentElement.dataset.theme = theme;
      try {
        localStorage.setItem("docusight-theme", theme);
      } catch {}
    }
  }, [theme]);
  const toggleTheme = () => {
    const isDark = theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    setTheme(isDark ? "light" : "dark");
  };

  const upload = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    setUploadError(null);
    try {
      const created = await api.upload(files);
      await refresh();
      if (created[0]) {
        setActiveId(created[0].id);
        setTab("overview");
        setPage(null);
        setPane("doc");
      }
    } catch (e) {
      setUploadError((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const active = useMemo(() => docs.find((d) => d.id === activeId) ?? null, [docs, activeId]);
  const readyDocs = docs.filter((d) => d.status === "ready");
  const scopeIds = [...scope].filter((id) => readyDocs.some((d) => d.id === id));

  const ask = (q: string) => {
    const extra = active && active.status === "ready" ? [active.id] : [];
    if (extra.length) setScope((s) => new Set([...s, ...extra]));
    chat.current?.ask(q, extra);
    setPane("chat");
  };
  const openPage = (docId: string, p: number) => {
    setActiveId(docId);
    setTab("pages");
    setPage(p);
    setPane("doc");
  };

  const tabs: { id: Tab; label: string; icon: typeof LayoutDashboard; badge?: number }[] = [
    { id: "overview", label: "Overview", icon: LayoutDashboard },
    { id: "data", label: "Tables & Data", icon: Database, badge: active?.tables.length },
    { id: "pages", label: active?.kind === "pdf" ? "Pages" : "Sheets", icon: FileStack },
    { id: "lab", label: "Retrieval Lab", icon: FlaskConical },
  ];

  return (
    <div className="flex h-dvh flex-col">
      {/* header */}
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line bg-panel px-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-white">
            <ScanEye className="h-4.5 w-4.5" />
          </div>
          <div>
            <div className="text-[15px] font-semibold leading-tight text-ink">DocuSight</div>
            <div className="hidden text-[11px] leading-tight text-muted sm:block">Multimodal RAG for PDFs & spreadsheets</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {health && (
            <div className="hidden items-center gap-1.5 md:flex">
              <Badge tone={health.llm.available ? "good" : "warn"} className="text-[11px]">
                {health.llm.available ? health.llm.model : "LLM: no API key"}
              </Badge>
              <Badge tone={health.colpali.available ? "good" : "neutral"}>
                <Eye className="h-3 w-3" /> ColPali {health.colpali.available ? "on" : "off"}
              </Badge>
            </div>
          )}
          <button onClick={toggleTheme} className="rounded-lg p-2 text-ink-2 hover:bg-panel-2" aria-label="Toggle theme">
            <Sun className="hidden h-4 w-4 dark:block" />
            <Moon className="h-4 w-4 dark:hidden" />
          </button>
        </div>
      </header>

      {backendDown && (
        <div className="flex items-center gap-2 bg-bad-soft px-4 py-2 text-sm text-bad">
          <AlertCircle className="h-4 w-4" /> Can&apos;t reach the backend at {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}. Start it with{" "}
          <code className="rounded bg-panel px-1">./run.sh</code> or <code className="rounded bg-panel px-1">uvicorn app.main:app</code>.
        </div>
      )}
      {uploadError && (
        <div className="flex items-center gap-2 bg-bad-soft px-4 py-2 text-sm text-bad">
          <AlertCircle className="h-4 w-4" /> {uploadError}
          <button className="ml-auto underline" onClick={() => setUploadError(null)}>
            dismiss
          </button>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[272px_minmax(0,1fr)_420px] xl:grid-cols-[288px_minmax(0,1fr)_460px]">
        {/* sidebar */}
        <div className={cn("min-h-0 border-r border-line bg-panel", pane === "docs" ? "block" : "hidden lg:block")}>
          <Sidebar
            docs={docs}
            activeId={activeId}
            scope={scope}
            uploading={uploading}
            onUpload={upload}
            onSelect={(id) => {
              setActiveId(id);
              setPage(null);
              setPane("doc");
            }}
            onToggleScope={(id) =>
              setScope((s) => {
                const n = new Set(s);
                if (n.has(id)) n.delete(id);
                else n.add(id);
                return n;
              })
            }
            onDelete={async (id) => {
              await api.remove(id);
              knownReady.current.delete(id);
              setScope((s) => {
                const n = new Set(s);
                n.delete(id);
                return n;
              });
              refresh();
            }}
          />
        </div>

        {/* workspace */}
        <main className={cn("min-h-0 min-w-0 flex-col", pane === "doc" ? "flex" : "hidden lg:flex")}>
          {active ? (
            <>
              <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-line bg-panel px-3">
                {tabs.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => {
                      setTab(t.id);
                      if (t.id === "pages") setPage(null);
                    }}
                    className={cn(
                      "flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-3 text-sm font-medium transition-colors",
                      tab === t.id ? "border-accent text-ink" : "border-transparent text-ink-2 hover:text-ink",
                    )}
                  >
                    <t.icon className="h-4 w-4" /> {t.label}
                    {!!t.badge && <span className="rounded bg-panel-2 px-1.5 text-[11px] text-ink-2">{t.badge}</span>}
                  </button>
                ))}
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto p-4 lg:p-6">
                {active.status === "processing" && tab !== "lab" ? (
                  <div className="mx-auto flex max-w-md flex-col items-center gap-4 py-20 text-center">
                    <Spinner className="h-8 w-8 text-accent" />
                    <div>
                      <p className="font-semibold text-ink">Processing {active.filename}</p>
                      <p className="mt-1 text-sm text-ink-2">{active.stage}</p>
                    </div>
                    <Progress value={active.progress} />
                    <p className="text-xs text-muted">Parsing layout, detecting tables, building BM25 + dense + visual indexes…</p>
                  </div>
                ) : active.status === "error" ? (
                  <Empty icon={<AlertCircle className="h-8 w-8 text-bad" />} title="Processing failed">
                    {active.error}
                  </Empty>
                ) : tab === "overview" ? (
                  <OverviewTab doc={active} onAsk={ask} onOpenPage={(p) => openPage(active.id, p)} />
                ) : tab === "data" ? (
                  <DataTab key={active.id} doc={active} />
                ) : tab === "pages" ? (
                  <PagesTab key={active.id} doc={active} page={page} setPage={setPage} />
                ) : (
                  <SearchLab docs={readyDocs} colpaliAvailable={!!health?.colpali.available} onOpenPage={openPage} />
                )}
              </div>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center p-6">
              <Empty icon={<UploadCloud className="h-10 w-10 text-accent" />} title="Upload a PDF or spreadsheet to begin">
                DocuSight instantly extracts every key field, table and metric — then answers any question with cited sources. Invoices, contracts,
                financial statements, scanned forms and messy multi-sheet workbooks all work.
              </Empty>
            </div>
          )}
        </main>

        {/* chat */}
        <div className={cn("min-h-0 border-l border-line bg-panel", pane === "chat" ? "block" : "hidden lg:block")}>
          <ChatPanel
            docs={docs}
            scope={scopeIds}
            ref={chat}
            llmAvailable={!!health?.llm.available}
            colpaliAvailable={!!health?.colpali.available}
          />
        </div>
      </div>

      {/* mobile pane switcher */}
      <nav className="grid shrink-0 grid-cols-3 border-t border-line bg-panel lg:hidden">
        {(
          [
            ["docs", "Documents", FileStack],
            ["doc", "Workspace", LayoutDashboard],
            ["chat", "Chat", MessageSquare],
          ] as const
        ).map(([id, label, Icon]) => (
          <button key={id} onClick={() => setPane(id)} className={cn("flex flex-col items-center gap-0.5 py-2 text-[11px]", pane === id ? "text-accent" : "text-ink-2")}>
            <Icon className="h-5 w-5" /> {label}
          </button>
        ))}
      </nav>
    </div>
  );
}
