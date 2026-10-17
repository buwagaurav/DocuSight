export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface ColumnInfo {
  name: string;
  type: string;
  non_null: number;
  min?: number | null;
  max?: number | null;
  mean?: number | null;
  sum?: number | null;
  distinct?: number;
  top?: string[];
}

export interface TableMeta {
  name: string;
  title: string;
  source: string;
  page?: number | null;
  sheet?: string | null;
  n_rows: number;
  n_cols: number;
  columns: ColumnInfo[];
}

export interface SheetMeta {
  index: number;
  name: string;
  rows: number;
  cols: number;
  tables: string[];
  merged?: number;
  formulas?: number;
  hidden?: number;
}

export interface KeyField {
  label: string;
  value: string;
  category: string;
  importance: "critical" | "high" | "normal";
  source: string;
}

export interface Extraction {
  document_type: string;
  title: string;
  summary: string;
  language: string;
  key_fields: KeyField[];
  metrics: { label: string; value: string; unit: string; context: string; source: string }[];
  entities: { name: string; type: string; role: string }[];
  dates: { label: string; date: string; source: string }[];
  sections: { title: string; description: string; source: string }[];
  insights: string[];
  anomalies: string[];
  suggested_questions: string[];
  _engine?: string;
  _error?: string;
}

export interface Doc {
  id: string;
  filename: string;
  kind: "pdf" | "excel" | "csv";
  status: "processing" | "ready" | "error";
  stage: string;
  progress: number;
  error: string | null;
  created_at: number;
  n_pages: number;
  n_chunks: number;
  sheets: SheetMeta[];
  tables: TableMeta[];
  visual_index: "disabled" | "pending" | "building" | "ready" | "error";
  extraction_status: "pending" | "running" | "ready" | "error" | "skipped";
  extraction: Extraction | null;
  techniques: string[];
}

export interface Health {
  ok: boolean;
  llm: { available: boolean; model: string };
  colpali: { available: boolean; model: string; error: string | null };
  embedding_model: string;
  rerank_model: string;
}

export interface ExtractionField {
  id: string;
  name: string;
  description: string;
  instructions: string;
  data_type: "string" | "number" | "integer" | "boolean" | "date" | "object";
  nullable: boolean;
  required: boolean;
  unit: string;
  normalization: string;
  allowed_values: string[];
  multiple: boolean;
  constraints: Record<string, unknown>;
}

export interface ExtractionCell {
  field_id: string;
  document_id: string;
  value: string;
  raw_value: string;
  normalized_value: unknown;
  status: "supported" | "missing" | "illegible" | "not_applicable" | "conflicting" | "unsupported";
  evidence: { text: string; page: number | null; start_char?: number | null; end_char?: number | null; verified?: boolean | null; document_id?: string; filename?: string }[];
  validation_issues: string[];
  review_status: string;
  original_model_value?: { value: string; normalized_value: unknown; status: string } | null;
  previous_reviewed_value?: { value: string; normalized_value: unknown; corrected_at?: number } | null;
}

export interface ExtractionObservation {
  id: string;
  record_key: string;
  label: string;
  document_id: string;
  filename: string;
  observation_unit: string;
  fields: Record<string, ExtractionCell>;
}

export interface ExtractionProject {
  id: string;
  name: string;
  objective: string;
  observation_unit: string;
  documents: string[];
  schema: { version?: number; fields: ExtractionField[]; json_schema?: Record<string, unknown> };
  proposed_schema?: { fields: ExtractionField[] };
  schema_version: number;
  schema_versions: unknown[];
  discovery_reasoning?: string;
  observations?: ExtractionObservation[];
  observation_count?: number;
  status: string;
  job: { id?: string | null; status: string; stage: string; progress: number; error?: string | null; document_done?: number; document_total?: number; document_ids?: string[] };
  created_at: number;
  updated_at: number;
}

export interface ExtractionCapabilities {
  cloud_enabled: boolean;
  llm_available: boolean;
  provider: string;
  local_generation_available: boolean;
  vision_available: boolean;
  cloud_consent_required: boolean;
  cost_pricing_available: boolean;
}

export interface Source {
  id: string;
  kind: "text" | "page" | "sql";
  doc_id: string;
  filename?: string;
  page?: number | null;
  sheet?: string | null;
  section?: string | null;
  snippet?: string;
  score?: number;
  signals?: Record<string, number>;
  sql?: string;
  columns?: string[];
  rows?: unknown[][];
}

export interface ChartSpec {
  chart_type: "bar" | "line" | "area" | "pie" | "scatter";
  title: string;
  data: Record<string, unknown>[];
  x_key: string;
  y_keys: string[];
  y_label?: string;
}

export type ChatEvent =
  | { type: "text"; delta: string }
  | { type: "thinking"; delta: string }
  | { type: "status"; text: string }
  | { type: "sources"; sources: Source[] }
  | { type: "chart"; chart: ChartSpec }
  | { type: "tool_start"; id: string; name: string; label: string; input: Record<string, unknown> }
  | { type: "tool_end"; id: string; ok: boolean; detail: Record<string, unknown> }
  | { type: "done"; cached: boolean; techniques: string[] }
  | { type: "error"; message: string };

export interface ChatOptions {
  effort: "low" | "medium" | "high";
  colpali: boolean;
  rerank: boolean;
  vision: boolean;
  adaptive: boolean;
  cache: boolean;
}

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      msg = body.detail ?? msg;
    } catch {}
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetch(`${API}/api/health`).then((r) => j<Health>(r)),
  docs: () => fetch(`${API}/api/documents`).then((r) => j<Doc[]>(r)),
  upload: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return fetch(`${API}/api/documents`, { method: "POST", body: fd }).then((r) => j<Doc[]>(r));
  },
  remove: (id: string) => fetch(`${API}/api/documents/${id}`, { method: "DELETE" }).then((r) => j(r)),
  reExtract: (id: string) => fetch(`${API}/api/documents/${id}/extract`, { method: "POST" }).then((r) => j(r)),
  pageUrl: (id: string, page: number) => `${API}/api/documents/${id}/pages/${page}`,
  fileUrl: (id: string) => `${API}/api/documents/${id}/file`,
  sheet: (id: string, index: number) =>
    fetch(`${API}/api/documents/${id}/sheets/${index}`).then((r) =>
      j<{ name: string; rows: string[][]; total_rows: number; total_cols: number }>(r),
    ),
  table: (id: string, name: string, limit = 2000) =>
    fetch(`${API}/api/documents/${id}/tables/${name}?limit=${limit}`).then((r) =>
      j<{ columns: string[]; rows: unknown[][]; total: number }>(r),
    ),
  sql: (id: string, sql: string) =>
    fetch(`${API}/api/documents/${id}/sql`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
    }).then((r) => j<{ columns: string[]; rows: unknown[][]; row_count: number; truncated: boolean }>(r)),
  search: (query: string, doc_ids: string[], colpali: boolean, rerank: boolean) =>
    fetch(`${API}/api/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, doc_ids, colpali, rerank, k: 10 }),
    }).then((r) =>
      j<{
        hits: (Source & { text: string; kind: string; score: number; signals: Record<string, number> })[];
        pages: { doc_id: string; page: number; score: number; filename: string }[];
        trace: Record<string, unknown>;
      }>(r),
    ),
  extractionCapabilities: () => fetch(`${API}/api/extraction/capabilities`).then((r) => j<ExtractionCapabilities>(r)),
  extractionProjects: () => fetch(`${API}/api/extraction/projects`).then((r) => j<(ExtractionProject & { observation_count: number; field_count: number })[]>(r)),
  extractionProject: (id: string) => fetch(`${API}/api/extraction/projects/${id}`).then((r) => j<ExtractionProject>(r)),
  createExtractionProject: (name: string, objective: string) =>
    fetch(`${API}/api/extraction/projects`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, objective }) }).then((r) => j<ExtractionProject>(r)),
  patchExtractionProject: (id: string, body: Partial<Pick<ExtractionProject, "name" | "objective" | "observation_unit">>) =>
    fetch(`${API}/api/extraction/projects/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) }).then((r) => j<ExtractionProject>(r)),
  uploadExtractionDocuments: (id: string, files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return fetch(`${API}/api/extraction/projects/${id}/documents`, { method: "POST", body: fd }).then((r) => j<Doc[]>(r));
  },
  extractionDocuments: (id: string) => fetch(`${API}/api/extraction/projects/${id}/documents`).then((r) => j<Doc[]>(r)),
  discoverExtractionSchema: (id: string, body: { objective: string; observation_unit: string; cloud_consent: boolean }) =>
    fetch(`${API}/api/extraction/projects/${id}/discover`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) }).then((r) => j<{ observation_unit: string; reasoning: string; fields: ExtractionField[] }>(r)),
  saveExtractionSchema: (id: string, body: { objective: string; observation_unit: string; fields: ExtractionField[] }) =>
    fetch(`${API}/api/extraction/projects/${id}/schema`, { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) }).then((r) => j<ExtractionProject>(r)),
  reuseExtractionSchema: (id: string, source_project_id: string) =>
    fetch(`${API}/api/extraction/projects/${id}/reuse-schema`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_project_id }) }).then((r) => j<ExtractionProject>(r)),
  extractionResults: (id: string, options: { limit?: number; offset?: number; q?: string; sort_by?: "document" | "observation" }) => {
    const params = new URLSearchParams({ limit: String(options.limit ?? 100), offset: String(options.offset ?? 0),
      q: options.q ?? "", sort_by: options.sort_by ?? "document" });
    return fetch(`${API}/api/extraction/projects/${id}/results?${params}`).then((r) =>
      j<{ total: number; all_total: number; offset: number; limit: number; observations: ExtractionObservation[] }>(r));
  },
  extractionEstimate: (id: string) => fetch(`${API}/api/extraction/projects/${id}/estimate`).then((r) =>
    j<{ ready_documents: number; pages: number; estimated_input_tokens: number; estimated_cost: number | null; note: string }>(r)),
  startExtraction: (id: string, body: { cloud_consent: boolean; document_ids?: string[] }) =>
    fetch(`${API}/api/extraction/projects/${id}/extract`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) }).then((r) => j<ExtractionProject>(r)),
  rerunExtraction: (id: string, body: { cloud_consent: boolean; document_ids?: string[]; field_ids?: string[]; observation_ids?: string[] }) =>
    fetch(`${API}/api/extraction/projects/${id}/rerun`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) }).then((r) => j<ExtractionProject>(r)),
  resumeExtraction: (id: string, cloud_consent: boolean) =>
    fetch(`${API}/api/extraction/projects/${id}/resume`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cloud_consent }) }).then((r) => j<ExtractionProject>(r)),
  cancelExtraction: (id: string) => fetch(`${API}/api/extraction/projects/${id}/cancel`, { method: "POST" }).then((r) => j<ExtractionProject>(r)),
  correctExtractionCell: (id: string, observationId: string, fieldId: string, body: { value: string; normalized_value: unknown; status: string }) =>
    fetch(`${API}/api/extraction/projects/${id}/results/${observationId}/fields/${fieldId}`, { method: "PATCH",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => j<ExtractionCell>(r)),
  deleteExtractionProject: (id: string) => fetch(`${API}/api/extraction/projects/${id}`, { method: "DELETE" }).then((r) => j<{ ok: boolean }>(r)),
  extractionExportUrl: (id: string, format: "json" | "csv" | "xlsx") => `${API}/api/extraction/projects/${id}/export/${format}`,
};

/** POST /api/chat and yield server-sent events. */
export async function* streamChat(
  body: { question: string; history: { role: string; content: string }[]; doc_ids: string[]; options: ChatOptions },
  signal: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${API}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`Chat request failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = raw.split("\n").find((l) => l.startsWith("data: "));
      if (line) yield JSON.parse(line.slice(6)) as ChatEvent;
    }
  }
}
