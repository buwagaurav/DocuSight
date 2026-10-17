"use client";

import { AlertCircle, ArrowDown, ArrowUp, Check, Download, FilePlus2, FileText, Plus, RefreshCw, Save, Search, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Doc, type ExtractionCapabilities, type ExtractionField, type ExtractionObservation, type ExtractionProject } from "@/lib/api";
import { Badge, Button, Card, Progress, Spinner, cn } from "./ui";

type ProjectListItem = ExtractionProject & { observation_count: number; field_count: number };
type ActiveCell = { observationId: string; fieldId: string };

function fieldId() {
  return `field_${Math.random().toString(36).slice(2, 11)}`;
}

function display(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function statusTone(status: string): "good" | "warn" | "bad" | "neutral" {
  if (status === "supported") return "good";
  if (status === "conflicting" || status === "illegible") return "bad";
  if (status === "missing" || status === "not_applicable") return "warn";
  return "neutral";
}

function parseNormalized(value: string, field: ExtractionField): unknown {
  if (!value.trim()) return null;
  if (field.multiple) return JSON.parse(value);
  if (field.data_type === "number") return Number(value.replaceAll(",", ""));
  if (field.data_type === "integer") return Number.parseInt(value.replaceAll(",", ""), 10);
  if (field.data_type === "boolean") return value.toLowerCase() === "true";
  if (field.data_type === "object") return JSON.parse(value);
  return value;
}

function blankField(): ExtractionField {
  return { id: fieldId(), name: "New field", description: "", instructions: "", data_type: "string", nullable: true,
    required: false, unit: "", normalization: "", allowed_values: [], multiple: false, constraints: {} };
}

export default function ExtractionWorkspace() {
  const [capabilities, setCapabilities] = useState<ExtractionCapabilities | null>(null);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [project, setProject] = useState<ExtractionProject | null>(null);
  const [documents, setDocuments] = useState<Doc[]>([]);
  const [observations, setObservations] = useState<ExtractionObservation[]>([]);
  const [resultTotal, setResultTotal] = useState(0);
  const [allResultTotal, setAllResultTotal] = useState(0);
  const [resultOffset, setResultOffset] = useState(0);
  const [estimate, setEstimate] = useState<{ ready_documents: number; pages: number; estimated_input_tokens: number; estimated_cost: number | null; note: string } | null>(null);
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [observationUnit, setObservationUnit] = useState("");
  const [fields, setFields] = useState<ExtractionField[]>([]);
  const [cloudConsent, setCloudConsent] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [activeCell, setActiveCell] = useState<ActiveCell | null>(null);
  const [editValue, setEditValue] = useState("");
  const [editNormalized, setEditNormalized] = useState("");
  const [filter, setFilter] = useState("");
  const [sortBy, setSortBy] = useState<"document" | "observation">("document");
  const [schemaSource, setSchemaSource] = useState("");

  const refreshProjectList = useCallback(async () => {
    setProjects(await api.extractionProjects());
  }, []);

  const loadProject = useCallback(async (id: string) => {
    const [p, docs, estimateResult, resultPage] = await Promise.all([
      api.extractionProject(id), api.extractionDocuments(id), api.extractionEstimate(id),
      api.extractionResults(id, { limit: 100, offset: 0, q: "", sort_by: "document" }),
    ]);
    setProject(p);
    setDocuments(docs);
    setEstimate(estimateResult);
    setObservations(resultPage.observations);
    setResultTotal(resultPage.total);
    setAllResultTotal(resultPage.all_total);
    setResultOffset(0);
    setFilter("");
    setSortBy("document");
    setName(p.name);
    setObjective(p.objective);
    setObservationUnit(p.observation_unit);
    setFields(p.schema?.fields?.length ? p.schema.fields : p.proposed_schema?.fields ?? []);
    setActiveCell(null);
    setCloudConsent(false);
    await refreshProjectList();
  }, [refreshProjectList]);

  useEffect(() => {
    Promise.all([api.extractionCapabilities(), api.extractionProjects()])
      .then(([caps, list]) => { setCapabilities(caps); setProjects(list); })
      .catch((e) => setError((e as Error).message));
  }, []);

  const currentProjectId = project?.id;
  const refreshCurrent = useCallback(async () => {
    if (!currentProjectId) return;
    try {
      const [p, docs, estimateResult, resultPage] = await Promise.all([
        api.extractionProject(currentProjectId), api.extractionDocuments(currentProjectId), api.extractionEstimate(currentProjectId),
        api.extractionResults(currentProjectId, { limit: 100, offset: resultOffset, q: filter, sort_by: sortBy }),
      ]);
      setProject(p);
      setDocuments(docs);
      setEstimate(estimateResult);
      setObservations(resultPage.observations);
      setResultTotal(resultPage.total);
      setAllResultTotal(resultPage.all_total);
      void refreshProjectList();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [currentProjectId, refreshProjectList, resultOffset, filter, sortBy]);

  const projectId = project?.id;
  useEffect(() => {
    if (!projectId) return;
    const timer = window.setTimeout(() => {
      api.extractionResults(projectId, { limit: 100, offset: resultOffset, q: filter, sort_by: sortBy })
        .then((resultPage) => { setObservations(resultPage.observations); setResultTotal(resultPage.total); setAllResultTotal(resultPage.all_total); })
        .catch((e) => setError((e as Error).message));
    }, 180);
    return () => window.clearTimeout(timer);
  }, [projectId, resultOffset, filter, sortBy]);

  const polling = !!project && (documents.some((d) => d.status === "processing") || ["queued", "running"].includes(project.job?.status));
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(() => void refreshCurrent(), 1800);
    return () => window.clearInterval(timer);
  }, [polling, refreshCurrent]);

  const selectedRow = useMemo(() => observations.find((row) => row.id === activeCell?.observationId) ?? null,
    [observations, activeCell]);
  const selectedField = useMemo(() => fields.find((field) => field.id === activeCell?.fieldId) ?? null, [fields, activeCell]);
  const selectedCell = selectedRow && activeCell ? selectedRow.fields[activeCell.fieldId] : null;

  const openCell = (row: ExtractionObservation, field: ExtractionField) => {
    const cell = row.fields[field.id];
    setActiveCell({ observationId: row.id, fieldId: field.id });
    setEditValue(cell?.value ?? "");
    setEditNormalized(display(cell?.normalized_value) === "—" ? "" : display(cell?.normalized_value));
  };

  const createProject = async () => {
    setBusy(true); setError(null);
    try {
      const created = await api.createExtractionProject(name || "Untitled extraction", objective);
      await loadProject(created.id);
      setNotice("Project created. Add PDFs to start.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const uploadFiles = async (files: FileList | null) => {
    if (!project || !files?.length) return;
    setUploading(true); setError(null); setNotice(null);
    try {
      await api.uploadExtractionDocuments(project.id, Array.from(files));
      await refreshCurrent();
      setNotice("PDFs added. Local parsing and indexing started.");
    } catch (e) { setError((e as Error).message); }
    finally { setUploading(false); }
  };

  const saveDetails = async () => {
    if (!project) return;
    setBusy(true); setError(null);
    try {
      const p = await api.patchExtractionProject(project.id, { name, objective, observation_unit: observationUnit });
      setProject(p); await refreshProjectList(); setNotice("Project details saved.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const discover = async () => {
    if (!project) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const proposed = await api.discoverExtractionSchema(project.id, { objective, observation_unit: observationUnit, cloud_consent: cloudConsent });
      setObservationUnit(proposed.observation_unit);
      setFields(proposed.fields);
      await refreshCurrent();
      setNotice("Schema proposal ready. Review and edit the fields, then save it.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const saveSchema = async () => {
    if (!project) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const saved = await api.saveExtractionSchema(project.id, { objective, observation_unit: observationUnit, fields });
      setProject(saved); setFields(saved.schema.fields); await refreshProjectList();
      setNotice(`Schema version ${saved.schema_version} saved.`);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const reuseSchema = async () => {
    if (!project || !schemaSource) return;
    setBusy(true); setError(null);
    try {
      const saved = await api.reuseExtractionSchema(project.id, schemaSource);
      setProject(saved); setFields(saved.schema.fields); setObservationUnit(saved.observation_unit);
      await refreshProjectList(); setNotice(`Copied schema into version ${saved.schema_version}. Review it before extraction.`);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const runExtraction = async () => {
    if (!project) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      await persistDefinition();
      await api.startExtraction(project.id, { cloud_consent: cloudConsent });
      await refreshCurrent(); setNotice("Extraction queued. Progress and results will update here.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const resumeExtraction = async () => {
    if (!project) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      await api.resumeExtraction(project.id, cloudConsent);
      await refreshCurrent(); setNotice("Extraction resumed from the first unfinished document.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const cancelExtraction = async () => {
    if (!project) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      await api.cancelExtraction(project.id);
      await refreshCurrent(); setNotice("Cancellation requested. The current model call will finish before the job stops.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const rerun = async (body: { document_ids?: string[]; field_ids?: string[]; observation_ids?: string[] }) => {
    if (!project) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      await persistDefinition();
      await api.rerunExtraction(project.id, { ...body, cloud_consent: cloudConsent });
      await refreshCurrent(); setNotice("Selected extraction work was queued.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const correctCell = async () => {
    if (!project || !selectedRow || !selectedField || !selectedCell) return;
    setBusy(true); setError(null);
    try {
      const normalized = parseNormalized(editNormalized, selectedField);
      await api.correctExtractionCell(project.id, selectedRow.id, selectedField.id,
        { value: editValue, normalized_value: normalized, status: editValue.trim() ? "supported" : "missing" });
      await refreshCurrent(); setNotice("Correction saved; the original model result and evidence remain available.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const deleteProject = async () => {
    if (!project || !window.confirm(`Delete extraction project “${project.name}”? Its shared documents will remain in DocuSight.`)) return;
    setBusy(true); setError(null);
    try {
      await api.deleteExtractionProject(project.id);
      setProject(null); setDocuments([]); setFields([]); setActiveCell(null); await refreshProjectList();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  const updateField = (id: string, update: Partial<ExtractionField>) => setFields((all) => all.map((f) => f.id === id ? { ...f, ...update } : f));
  const moveField = (index: number, direction: -1 | 1) => setFields((all) => {
    const next = [...all]; const target = index + direction;
    if (target < 0 || target >= next.length) return all;
    [next[index], next[target]] = [next[target], next[index]]; return next;
  });

  const rows = observations;

  const docsReady = documents.length > 0 && documents.every((doc) => doc.status === "ready");
  const jobRunning = !!project && ["queued", "running"].includes(project.job.status);
  const schemaDirty = !!project && (JSON.stringify(fields) !== JSON.stringify(project.schema?.fields ?? []) ||
    observationUnit !== project.observation_unit || objective !== project.objective);

  const persistDefinition = async () => {
    if (!project) return;
    let saved = await api.patchExtractionProject(project.id, { name, objective, observation_unit: observationUnit });
    if (JSON.stringify(fields) !== JSON.stringify(project.schema?.fields ?? []) || observationUnit !== project.observation_unit || project.schema_version === 0) {
      saved = await api.saveExtractionSchema(project.id, { objective, observation_unit: observationUnit, fields });
      setFields(saved.schema.fields);
    }
    setProject(saved);
    await refreshProjectList();
  };

  return (
    <div className="space-y-4 pb-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-accent">Schema-guided extraction</p>
          <h1 className="mt-1 text-xl font-semibold text-ink">Turn document collections into reviewable data</h1>
          <p className="mt-1 text-sm text-ink-2">Discover one schema for a project, extract every record, then review values against their source pages.</p>
        </div>
        {project && <Button variant="danger" size="sm" onClick={deleteProject} disabled={busy || jobRunning}><Trash2 className="h-3.5 w-3.5" /> Delete project</Button>}
      </div>

      {error && <div className="flex items-start gap-2 rounded-lg border border-bad/30 bg-bad-soft p-3 text-sm text-bad"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}<button className="ml-auto" onClick={() => setError(null)} aria-label="Dismiss error"><X className="h-4 w-4" /></button></div>}
      {notice && <div className="flex items-center gap-2 rounded-lg border border-good/20 bg-good-soft p-3 text-sm text-good"><Check className="h-4 w-4" />{notice}<button className="ml-auto" onClick={() => setNotice(null)} aria-label="Dismiss notice"><X className="h-4 w-4" /></button></div>}

      {!capabilities?.cloud_enabled && (
        <div className="rounded-xl border border-warn/30 bg-warn-soft p-3 text-sm text-warn">
          <strong>Cloud extraction is disabled.</strong> Add <code>EXTRACTION_CLOUD_ENABLED=true</code> to the project-root <code>.env</code> and restart the backend after choosing your LLM provider. Extraction requires the existing configured provider; local schema generation is not available in this repository.
        </div>
      )}
      {capabilities?.cloud_enabled && !capabilities.llm_available && (
        <div className="rounded-xl border border-warn/30 bg-warn-soft p-3 text-sm text-warn">Set credentials for <strong>{capabilities.provider}</strong> in <code>.env</code>, then restart the backend.</div>
      )}
      {capabilities?.llm_available && !capabilities.vision_available && (
        <div className="rounded-xl border border-warn/30 bg-warn-soft p-3 text-sm text-warn">The configured model is text-only. Scanned PDFs need local OCR text; on Apple Silicon, install <code>backend/requirements-ocr.txt</code>. Digital PDFs continue to work.</div>
      )}

      {!project ? (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(280px,0.75fr)]">
          <Card className="p-4">
            <h2 className="font-semibold text-ink">Create an extraction project</h2>
            <p className="mt-1 text-sm text-ink-2">A project keeps its PDFs, objective, schema versions, results, and review history together.</p>
            <label className="mt-4 block text-xs font-medium text-ink-2">Project name</label>
            <input className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink" value={name} onChange={(e) => setName(e.target.value)} placeholder="Annual report review" />
            <label className="mt-3 block text-xs font-medium text-ink-2">Extraction objective</label>
            <textarea className="mt-1 min-h-24 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink" value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="Extract company, reporting year, revenue, net income, total assets, currency, and source page references." />
            <Button variant="primary" className="mt-3" disabled={busy} onClick={createProject}><FilePlus2 className="h-4 w-4" /> Create project</Button>
          </Card>
          <Card className="p-4">
            <h2 className="font-semibold text-ink">Reopen a saved project</h2>
            <p className="mt-1 text-sm text-ink-2">Projects and extraction results are stored under the configured DocuSight data directory.</p>
            <div className="mt-3 space-y-2">
              {projects.length === 0 ? <p className="rounded-lg bg-panel-2 p-3 text-sm text-muted">No saved projects yet.</p> : projects.map((item) => (
                <button key={item.id} onClick={() => loadProject(item.id).catch((e) => setError((e as Error).message))}
                  className="flex w-full items-center justify-between rounded-lg border border-line p-3 text-left hover:bg-panel-2">
                  <span className="min-w-0"><span className="block truncate text-sm font-medium text-ink">{item.name}</span><span className="mt-0.5 block truncate text-xs text-muted">{item.objective || "No objective yet"}</span></span>
                  <span className="ml-2 shrink-0 text-xs text-muted">{item.observation_count} rows · {item.field_count} fields</span>
                </button>
              ))}
            </div>
          </Card>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel p-3">
            <label className="text-xs font-medium text-ink-2">Project</label>
            <select className="max-w-full rounded-lg border border-line bg-bg px-2.5 py-1.5 text-sm text-ink" value={project.id}
              onChange={(e) => loadProject(e.target.value).catch((err) => setError((err as Error).message))}>
              {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <span className="ml-auto flex items-center gap-2 text-xs text-muted"><Badge>Schema v{project.schema_version || 0}</Badge><Badge tone={project.job.status === "ready" ? "good" : project.job.status === "error" ? "bad" : "neutral"}>{project.job.status}</Badge></span>
          </div>

          <div className="grid gap-4 2xl:grid-cols-2">
            <Card className="p-4">
              <div className="flex items-start justify-between gap-3"><div><h2 className="font-semibold text-ink">1. Add PDFs</h2><p className="mt-1 text-sm text-ink-2">Parsing, OCR when available, and indexing reuse DocuSight’s local ingestion pipeline.</p></div>
                <label className={cn("inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg bg-accent px-3 text-sm font-medium text-white", uploading && "pointer-events-none opacity-60")}>
                  {uploading ? <Spinner /> : <Plus className="h-4 w-4" />} Add PDFs
                  <input type="file" accept="application/pdf,.pdf" multiple className="hidden" disabled={uploading} onChange={(e) => { void uploadFiles(e.target.files); e.currentTarget.value = ""; }} />
                </label>
              </div>
              <div className="mt-3 space-y-2">
                {documents.length === 0 ? <div className="rounded-lg border border-dashed border-line p-5 text-center text-sm text-muted">Add one or more PDF files. A document may contain multiple records.</div> : documents.map((doc) => (
                  <div key={doc.id} className="flex items-center gap-3 rounded-lg border border-line p-2.5">
                    <FileText className="h-4 w-4 shrink-0 text-accent" />
                    <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium text-ink">{doc.filename}</div>
                      <div className="mt-0.5 text-xs text-muted">{doc.status === "processing" ? doc.stage : doc.status === "error" ? doc.error : `${doc.n_pages} pages · ${doc.n_chunks} indexed chunks`}</div>
                      {doc.status === "processing" && <Progress value={doc.progress} />}
                    </div>
                    <Badge tone={doc.status === "ready" ? "good" : doc.status === "error" ? "bad" : "neutral"}>{doc.status}</Badge>
                  </div>
                ))}
              </div>
            </Card>

            <Card className="p-4">
              <div className="flex items-center justify-between gap-2"><div><h2 className="font-semibold text-ink">2. Define the question and row</h2><p className="mt-1 text-sm text-ink-2">The row is one observation unit; a source PDF can contribute several rows.</p></div><Button size="sm" onClick={saveDetails} disabled={busy}><Save className="h-3.5 w-3.5" /> Save details</Button></div>
              <label className="mt-3 block text-xs font-medium text-ink-2">Project name</label>
              <input className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink" value={name} onChange={(e) => setName(e.target.value)} />
              <label className="mt-3 block text-xs font-medium text-ink-2">Extraction objective</label>
              <textarea className="mt-1 min-h-20 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink" value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="What facts should the result table contain?" />
              <label className="mt-3 block text-xs font-medium text-ink-2">Observation unit</label>
              <input className="mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink" value={observationUnit} onChange={(e) => setObservationUnit(e.target.value)} placeholder="e.g. one company reporting year" />
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <Button variant="primary" disabled={busy || !docsReady || !objective.trim() || !capabilities?.cloud_enabled || !capabilities?.llm_available || !cloudConsent}
                  onClick={discover}><Search className="h-4 w-4" /> Discover schema</Button>
                <label className="flex max-w-xl items-start gap-2 text-xs text-ink-2"><input className="mt-0.5 accent-[var(--accent)]" type="checkbox" checked={cloudConsent} onChange={(e) => setCloudConsent(e.target.checked)} />
                  I approve sending this project’s document text and scanned-page images to <strong>{capabilities?.provider ?? "the configured LLM provider"}</strong> for schema discovery and extraction.</label>
              </div>
              {!docsReady && documents.length > 0 && <p className="mt-2 text-xs text-muted">Wait for every uploaded PDF to finish processing before discovering a schema.</p>}
              {estimate && <p className="mt-2 text-xs text-muted">Ready documents: {estimate.ready_documents} · {estimate.pages.toLocaleString()} pages · roughly {estimate.estimated_input_tokens.toLocaleString()} text tokens. {estimate.note}</p>}
            </Card>
          </div>

          <Card className="p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div><h2 className="font-semibold text-ink">3. Review and edit the schema</h2><p className="mt-1 text-sm text-ink-2">Fields are versioned. Reorder, refine rules, add fields, or remove suggestions before extraction.</p></div>
              <div className="flex flex-wrap gap-2">
                {projects.some((item) => item.id !== project.id && item.schema_version > 0) && <><select className="max-w-48 rounded-lg border border-line bg-bg px-2 text-xs text-ink" value={schemaSource} onChange={(e) => setSchemaSource(e.target.value)}><option value="">Reuse saved schema…</option>{projects.filter((item) => item.id !== project.id && item.schema_version > 0).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><Button size="sm" disabled={busy || !schemaSource} onClick={reuseSchema}><RefreshCw className="h-3.5 w-3.5" /> Copy schema</Button></>}
                <Button size="sm" onClick={() => setFields((all) => [...all, blankField()])}><Plus className="h-3.5 w-3.5" /> Add field</Button><Button size="sm" variant="primary" disabled={busy || fields.length === 0} onClick={saveSchema}><Save className="h-3.5 w-3.5" /> Save schema</Button>
              </div>
            </div>
            {project.discovery_reasoning && <p className="mt-3 rounded-lg bg-panel-2 p-2.5 text-xs text-ink-2">Schema discovery: {project.discovery_reasoning}</p>}
            {fields.length === 0 ? <div className="mt-3 rounded-lg border border-dashed border-line p-5 text-center text-sm text-muted">Discover a schema or add fields manually.</div> : (
              <div className="mt-3 space-y-3">
                {fields.map((field, index) => (
                  <div key={field.id} className="grid gap-2 rounded-xl border border-line bg-bg p-3 xl:grid-cols-[minmax(180px,1fr)_150px_minmax(210px,1.5fr)_auto]">
                    <div className="space-y-2">
                      <label className="text-[11px] font-medium uppercase tracking-wide text-muted">Display name</label>
                      <input className="w-full rounded-md border border-line bg-panel px-2.5 py-1.5 text-sm text-ink" value={field.name} onChange={(e) => updateField(field.id, { name: e.target.value })} />
                      <input className="w-full rounded-md border border-line bg-panel px-2.5 py-1.5 text-xs text-ink-2" value={field.unit} placeholder="Unit (optional)" onChange={(e) => updateField(field.id, { unit: e.target.value })} />
                    </div>
                    <div className="space-y-2">
                      <label className="text-[11px] font-medium uppercase tracking-wide text-muted">Type</label>
                      <select className="w-full rounded-md border border-line bg-panel px-2 py-1.5 text-sm text-ink" value={field.data_type} onChange={(e) => updateField(field.id, { data_type: e.target.value as ExtractionField["data_type"] })}>
                        {(["string", "number", "integer", "boolean", "date", "object"] as const).map((type) => <option key={type} value={type}>{type}</option>)}
                      </select>
                      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-2">
                        <label className="flex items-center gap-1"><input type="checkbox" checked={field.required} onChange={(e) => updateField(field.id, { required: e.target.checked })} /> Required</label>
                        <label className="flex items-center gap-1"><input type="checkbox" checked={field.nullable} onChange={(e) => updateField(field.id, { nullable: e.target.checked })} /> Nullable</label>
                        <label className="flex items-center gap-1"><input type="checkbox" checked={field.multiple} onChange={(e) => updateField(field.id, { multiple: e.target.checked })} /> Multiple</label>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <textarea className="min-h-14 w-full rounded-md border border-line bg-panel px-2.5 py-1.5 text-xs text-ink" value={field.description} placeholder="Description" onChange={(e) => updateField(field.id, { description: e.target.value })} />
                      <textarea className="min-h-14 w-full rounded-md border border-line bg-panel px-2.5 py-1.5 text-xs text-ink" value={field.instructions} placeholder="Extraction instructions and normalization rules" onChange={(e) => updateField(field.id, { instructions: e.target.value })} />
                      <input className="w-full rounded-md border border-line bg-panel px-2.5 py-1.5 text-xs text-ink" value={field.normalization} placeholder="Normalization rule" onChange={(e) => updateField(field.id, { normalization: e.target.value })} />
                      <input className="w-full rounded-md border border-line bg-panel px-2.5 py-1.5 text-xs text-ink" value={field.allowed_values.join(", ")} placeholder="Allowed values, comma separated" onChange={(e) => updateField(field.id, { allowed_values: e.target.value.split(",").map((v) => v.trim()).filter(Boolean) })} />
                      <textarea className="min-h-12 w-full rounded-md border border-line bg-panel px-2.5 py-1.5 font-mono text-[11px] text-ink" aria-label={`${field.name} JSON Schema constraints`} defaultValue={JSON.stringify(field.constraints ?? {}, null, 2)} onBlur={(e) => { try { updateField(field.id, { constraints: JSON.parse(e.target.value) as Record<string, unknown> }); } catch { setError(`Constraints for ${field.name} must be valid JSON.`); } }} placeholder='Constraints JSON, e.g. {"minimum":0,"maximum":100}' />
                    </div>
                    <div className="flex items-start gap-1 xl:flex-col">
                      <Button size="sm" variant="ghost" disabled={index === 0} onClick={() => moveField(index, -1)} aria-label="Move field up"><ArrowUp className="h-3.5 w-3.5" /></Button>
                      <Button size="sm" variant="ghost" disabled={index === fields.length - 1} onClick={() => moveField(index, 1)} aria-label="Move field down"><ArrowDown className="h-3.5 w-3.5" /></Button>
                      <Button size="sm" variant="danger" onClick={() => setFields((all) => all.filter((f) => f.id !== field.id))} aria-label="Remove field"><Trash2 className="h-3.5 w-3.5" /></Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {fields.length > 0 && <div className="mt-4 overflow-x-auto rounded-lg border border-line">
              <table className="w-full min-w-[520px] border-collapse text-left text-xs"><thead className="bg-panel-2 text-ink-2"><tr><th className="p-2.5">Expected observation</th>{fields.map((f) => <th key={f.id} className="p-2.5">{f.name}{f.unit ? ` (${f.unit})` : ""}</th>)}</tr></thead>
                <tbody><tr><td className="border-t border-line p-2.5 text-muted">Example values appear after extraction</td>{fields.map((f) => <td key={f.id} className="border-t border-line p-2.5 text-muted">{f.data_type}{f.required ? " · required" : " · optional"}</td>)}</tr></tbody>
              </table>
            </div>}
            {project.schema_version > 0 && <p className="mt-2 text-xs text-muted">Saved schema version {project.schema_version}; previous versions are retained with this project.</p>}
          </Card>

          <Card className="p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><h2 className="font-semibold text-ink">4. Extract, review, and export</h2><p className="mt-1 text-sm text-ink-2">Extraction processes all pages in bounded batches and stores each field with status, evidence, and validation results.</p></div>
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" disabled={busy || jobRunning || !docsReady || fields.length === 0 || !cloudConsent || !capabilities?.cloud_enabled || !capabilities?.llm_available} onClick={runExtraction}><RefreshCw className="h-4 w-4" /> {schemaDirty ? "Save and extract" : "Start extraction"}</Button>
                {jobRunning && <Button variant="danger" disabled={busy} onClick={cancelExtraction}><X className="h-4 w-4" /> Cancel job</Button>}
                {["error", "cancelled"].includes(project.job.status) && (project.job.document_done ?? 0) < (project.job.document_total ?? 0) && <Button size="sm" disabled={busy || !cloudConsent || !capabilities?.cloud_enabled || !capabilities?.llm_available} onClick={resumeExtraction}><RefreshCw className="h-3.5 w-3.5" /> Resume unfinished work</Button>}
                <a href={api.extractionExportUrl(project.id, "json")} onClick={(e) => { if (!allResultTotal) e.preventDefault(); }}><Button size="sm" disabled={allResultTotal === 0}><Download className="h-3.5 w-3.5" /> JSON</Button></a>
                <a href={api.extractionExportUrl(project.id, "csv")} onClick={(e) => { if (!allResultTotal) e.preventDefault(); }}><Button size="sm" disabled={allResultTotal === 0}><Download className="h-3.5 w-3.5" /> CSV</Button></a>
                <a href={api.extractionExportUrl(project.id, "xlsx")} onClick={(e) => { if (!allResultTotal) e.preventDefault(); }}><Button size="sm" disabled={allResultTotal === 0}><Download className="h-3.5 w-3.5" /> Excel</Button></a>
              </div>
            </div>
            {jobRunning && <div className="mt-3 rounded-lg bg-accent-soft p-3"><div className="mb-1 flex justify-between gap-2 text-xs text-accent-ink"><span>{project.job.stage}</span><span>{Math.round(project.job.progress * 100)}% · {project.job.document_done ?? 0}/{project.job.document_total ?? documents.length} documents</span></div><Progress value={project.job.progress} /></div>}
            {project.job.status === "error" && <div className="mt-3 rounded-lg bg-bad-soft p-3 text-sm text-bad">{project.job.error}</div>}

            {allResultTotal > 0 && <div className="mt-4 grid min-w-0 gap-3 2xl:grid-cols-[minmax(0,1fr)_340px]">
              <div className="min-w-0">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <div className="relative min-w-[180px] flex-1"><Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted" /><input className="w-full rounded-lg border border-line bg-bg py-2 pl-8 pr-3 text-xs text-ink" value={filter} onChange={(e) => { setFilter(e.target.value); setResultOffset(0); }} placeholder="Filter observations or values" /></div>
                  <label className="flex items-center gap-1.5 text-xs text-ink-2">Sort <select className="rounded-md border border-line bg-bg px-2 py-1.5 text-xs text-ink" value={sortBy} onChange={(e) => { setSortBy(e.target.value as typeof sortBy); setResultOffset(0); }}><option value="document">Document</option><option value="observation">Observation</option></select></label>
                </div>
                <div className="max-h-[560px] overflow-auto rounded-lg border border-line">
                  <table className="w-full min-w-max border-collapse text-left text-xs">
                    <thead className="sticky top-0 z-10 bg-panel-2 text-ink-2"><tr><th className="p-2.5">Observation</th><th className="p-2.5">Document</th>{fields.map((field) => <th key={field.id} className="max-w-48 p-2.5">{field.name}</th>)}</tr></thead>
                    <tbody>{rows.map((row) => <tr key={row.id} className="hover:bg-panel-2/60">
                      <td className="border-t border-line p-2.5 font-medium text-ink">{row.label}</td><td className="max-w-40 truncate border-t border-line p-2.5 text-ink-2" title={row.filename}>{row.filename}</td>
                      {fields.map((field) => { const cell = row.fields[field.id]; const active = activeCell?.observationId === row.id && activeCell?.fieldId === field.id; return <td key={field.id} className="border-t border-line p-1.5">
                        <button onClick={() => openCell(row, field)} className={cn("flex w-full min-w-32 items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left hover:bg-accent-soft", active && "bg-accent-soft ring-1 ring-accent")}>
                          <span className="max-w-40 truncate text-ink" title={cell?.value}>{cell?.value || "—"}</span><Badge tone={statusTone(cell?.status ?? "missing")}>{cell?.status ?? "missing"}</Badge>
                        </button>{cell?.validation_issues?.length ? <span className="mt-0.5 block truncate px-2 text-[10px] text-warn" title={cell.validation_issues.join(" ")}>{cell.validation_issues.length} validation issue(s)</span> : null}
                      </td>; })}
                    </tr>)}</tbody>
                  </table>
                  {rows.length === 0 && <div className="p-6 text-center text-sm text-muted">No results match this filter.</div>}
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted"><span>Showing {rows.length ? resultOffset + 1 : 0}–{resultOffset + rows.length} of {resultTotal} matches · {allResultTotal} total observations</span><div className="flex items-center gap-2"><button className="rounded border border-line px-2 py-1 disabled:opacity-40" disabled={resultOffset === 0} onClick={() => setResultOffset(Math.max(0, resultOffset - 100))}>Previous</button><button className="rounded border border-line px-2 py-1 disabled:opacity-40" disabled={resultOffset + 100 >= resultTotal} onClick={() => setResultOffset(resultOffset + 100)}>Next</button></div></div>
              </div>

              <aside className="min-w-0 rounded-lg border border-line bg-bg p-3">
                {!selectedCell || !selectedRow || !selectedField ? <div className="flex h-full min-h-44 flex-col items-center justify-center text-center text-sm text-muted"><FileText className="mb-2 h-6 w-6" />Select a result cell to inspect its evidence.</div> : (
                  <div className="space-y-3">
                    <div><div className="text-[11px] font-semibold uppercase tracking-wide text-muted">Evidence review</div><h3 className="mt-1 font-semibold text-ink">{selectedField.name}</h3><p className="text-xs text-ink-2">{selectedRow.label} · {selectedRow.filename}</p></div>
                    <div className="flex flex-wrap items-center gap-1.5"><Badge tone={statusTone(selectedCell.status)}>{selectedCell.status}</Badge><Badge>{selectedCell.review_status}</Badge></div>
                    {selectedCell.evidence.length === 0 ? <p className="rounded-md bg-warn-soft p-2 text-xs text-warn">No supporting evidence was returned. Review this value before using it.</p> : selectedCell.evidence.map((evidence, index) => <div key={`${evidence.page}-${index}`} className="rounded-md border border-line bg-panel p-2.5">
                      <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-muted"><span className="truncate">{evidence.filename ?? selectedRow.filename}</span><span className="shrink-0">{evidence.page ? `Page ${evidence.page}` : "Page not identified"}</span></div>
                      <blockquote className="border-l-2 border-accent pl-2 text-xs leading-relaxed text-ink">{evidence.text || "No quote supplied"}</blockquote>
                      {evidence.page && <a className="mt-2 inline-flex text-xs font-medium text-accent underline" href={`${api.fileUrl(evidence.document_id ?? selectedRow.document_id)}#page=${evidence.page}`} target="_blank" rel="noreferrer">Open original PDF · page {evidence.page}</a>}
                    </div>)}
                    {selectedCell.validation_issues.length > 0 && <ul className="list-disc space-y-1 pl-4 text-xs text-warn">{selectedCell.validation_issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul>}
                    {selectedCell.original_model_value && <details className="rounded-md bg-panel-2 p-2 text-xs text-ink-2"><summary className="cursor-pointer">Original model result</summary><pre className="mt-2 whitespace-pre-wrap">{JSON.stringify(selectedCell.original_model_value, null, 2)}</pre></details>}
                    {selectedCell.previous_reviewed_value && <details className="rounded-md bg-panel-2 p-2 text-xs text-ink-2"><summary className="cursor-pointer">Reviewed value before rerun</summary><pre className="mt-2 whitespace-pre-wrap">{JSON.stringify(selectedCell.previous_reviewed_value, null, 2)}</pre></details>}
                    <label className="block text-xs font-medium text-ink-2">Reviewed value<input className="mt-1 w-full rounded-md border border-line bg-panel px-2.5 py-2 text-sm text-ink" value={editValue} onChange={(e) => setEditValue(e.target.value)} /></label>
                    <label className="block text-xs font-medium text-ink-2">Normalized value {selectedField.multiple ? "(JSON array)" : `(${selectedField.data_type})`}<input className="mt-1 w-full rounded-md border border-line bg-panel px-2.5 py-2 text-sm text-ink" value={editNormalized} onChange={(e) => setEditNormalized(e.target.value)} placeholder={selectedField.multiple ? '["value"]' : selectedField.data_type} /></label>
                    <Button variant="primary" size="sm" disabled={busy} onClick={correctCell}><Save className="h-3.5 w-3.5" /> Save correction</Button>
                    {selectedCell.evidence[0]?.page && <div className="overflow-hidden rounded-md border border-line bg-panel-2">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img className="max-h-80 w-full object-contain" src={api.pageUrl(selectedCell.evidence[0].document_id ?? selectedRow.document_id, selectedCell.evidence[0].page!)} alt={`Source page ${selectedCell.evidence[0].page}`} />
                    </div>}
                    <div className="flex flex-wrap gap-2 border-t border-line pt-3">
                      <Button size="sm" disabled={busy || jobRunning || !cloudConsent} onClick={() => rerun({ observation_ids: [selectedRow.id], field_ids: [selectedField.id] })}><RefreshCw className="h-3.5 w-3.5" /> Rerun this field</Button>
                      <Button size="sm" disabled={busy || jobRunning || !cloudConsent} onClick={() => rerun({ document_ids: [selectedRow.document_id] })}><RefreshCw className="h-3.5 w-3.5" /> Rerun document</Button>
                    </div>
                  </div>
                )}
              </aside>
            </div>}
            {allResultTotal === 0 && !jobRunning && <div className="mt-4 rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">Save your schema, confirm the cloud-processing notice above, and start extraction. Real results will appear here.</div>}
          </Card>
        </>
      )}
    </div>
  );
}
