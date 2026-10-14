"use client";

import { AlertCircle, CheckCircle2, Eye, FileSpreadsheet, FileText, Trash2, UploadCloud } from "lucide-react";
import { useDropzone } from "react-dropzone";
import type { Doc } from "@/lib/api";
import { Badge, Progress, Spinner, cn } from "./ui";

const ACCEPT = {
  "application/pdf": [".pdf"],
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "application/vnd.ms-excel.sheet.macroEnabled.12": [".xlsm"],
  "application/vnd.ms-excel": [".xls"],
  "application/vnd.oasis.opendocument.spreadsheet": [".ods"],
  "text/csv": [".csv"],
  "text/tab-separated-values": [".tsv"],
};

export default function Sidebar({
  docs,
  activeId,
  scope,
  uploading,
  onUpload,
  onSelect,
  onToggleScope,
  onDelete,
}: {
  docs: Doc[];
  activeId: string | null;
  scope: Set<string>;
  uploading: boolean;
  onUpload: (files: File[]) => void;
  onSelect: (id: string) => void;
  onToggleScope: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const { getRootProps, getInputProps, isDragActive } = useDropzone({ accept: ACCEPT, onDrop: onUpload, multiple: true });

  return (
    <aside className="flex h-full min-h-0 flex-col">
      <div className="p-3">
        <div
          {...getRootProps()}
          className={cn(
            "cursor-pointer rounded-xl border-2 border-dashed px-4 py-5 text-center transition-colors",
            isDragActive ? "border-accent bg-accent-soft" : "border-line hover:border-accent hover:bg-panel-2",
          )}
        >
          <input {...getInputProps()} aria-label="Upload documents" />
          {uploading ? <Spinner className="mx-auto h-6 w-6 text-accent" /> : <UploadCloud className="mx-auto h-6 w-6 text-accent" />}
          <p className="mt-2 text-sm font-medium text-ink">{isDragActive ? "Drop to analyse" : "Drop PDF or Excel files"}</p>
          <p className="mt-0.5 text-xs text-muted">or click to browse · PDF, XLSX, XLS, CSV</p>
        </div>
      </div>

      <div className="flex items-center justify-between px-4 pb-1.5 pt-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">Documents ({docs.length})</span>
        <span className="text-[11px] text-muted" title="Checked documents are used by the chat">
          ✓ = in chat scope
        </span>
      </div>

      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {docs.length === 0 && (
          <li className="px-3 py-8 text-center text-sm text-muted">No documents yet. Upload one to get an instant extraction.</li>
        )}
        {docs.map((d) => {
          const Icon = d.kind === "pdf" ? FileText : FileSpreadsheet;
          const active = d.id === activeId;
          return (
            <li key={d.id}>
              <div
                role="button"
                tabIndex={0}
                onClick={() => onSelect(d.id)}
                onKeyDown={(e) => e.key === "Enter" && onSelect(d.id)}
                className={cn(
                  "group flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2 transition-colors",
                  active ? "bg-accent-soft" : "hover:bg-panel-2",
                )}
              >
                <input
                  type="checkbox"
                  className="mt-1 h-3.5 w-3.5 accent-[var(--accent)]"
                  checked={scope.has(d.id)}
                  disabled={d.status !== "ready"}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => onToggleScope(d.id)}
                  aria-label={`Include ${d.filename} in chat`}
                />
                <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", d.kind === "pdf" ? "text-[var(--series-8)]" : "text-[var(--series-6)]")} />
                <div className="min-w-0 flex-1">
                  <div className={cn("truncate text-sm", active ? "font-semibold text-accent-ink" : "font-medium text-ink")} title={d.filename}>
                    {d.filename}
                  </div>
                  {d.status === "processing" ? (
                    <div className="mt-1.5 space-y-1">
                      <Progress value={d.progress} />
                      <div className="truncate text-[11px] text-muted">{d.stage}</div>
                    </div>
                  ) : d.status === "error" ? (
                    <div className="mt-0.5 flex items-center gap-1 text-[11px] text-bad" title={d.error ?? ""}>
                      <AlertCircle className="h-3 w-3" /> Failed
                    </div>
                  ) : (
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      <span className="text-[11px] text-muted">
                        {d.kind === "pdf" ? `${d.n_pages} pages` : `${d.sheets.length} sheet${d.sheets.length === 1 ? "" : "s"}`}
                        {d.tables.length > 0 && ` · ${d.tables.length} tables`}
                      </span>
                      {d.extraction_status === "running" || d.extraction_status === "pending" ? (
                        <Badge tone="accent">
                          <Spinner className="h-2.5 w-2.5" /> extracting
                        </Badge>
                      ) : (
                        <CheckCircle2 className="h-3 w-3 text-good" aria-label="Extracted" />
                      )}
                      {(d.visual_index === "building" || d.visual_index === "pending") && (
                        <Badge tone="neutral">
                          <Eye className="h-2.5 w-2.5" /> ColPali…
                        </Badge>
                      )}
                    </div>
                  )}
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (confirm(`Delete ${d.filename}?`)) onDelete(d.id);
                  }}
                  className="rounded p-1 text-muted opacity-0 hover:bg-bad-soft hover:text-bad group-hover:opacity-100 focus:opacity-100"
                  aria-label={`Delete ${d.filename}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
