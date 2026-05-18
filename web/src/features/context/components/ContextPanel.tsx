import { useState } from "react";
import { FileText, ListTree, Paperclip, X, Eye, FolderOpen, Clock } from "lucide-react";
import type { FilePreview, GeneratedArtifact, UploadedFileItem, WorkflowPlan } from "@/types/app";
import { isPreviewable } from "@/features/chat/components/DocumentPreviewDialog";
import { DocumentPreviewDialog } from "@/features/chat/components/DocumentPreviewDialog";
import { ScheduledTasksPanel } from "@/features/scheduler/components/ScheduledTasksPanel";

function SparkleIcon({ size = 12, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
      <path d="M12 0L13.5 8.5L22 6L15 12L22 18L13.5 15.5L12 24L10.5 15.5L2 18L9 12L2 6L10.5 8.5L12 0Z" fill="currentColor" />
    </svg>
  );
}

interface ContextPanelProps {
  sessionId: string;
  files: UploadedFileItem[];
  workflow?: WorkflowPlan | null;
  selectedPreview?: FilePreview | null;
  onPreviewFile: (fileId: string) => void;
  onClosePreview: () => void;
}

export function ContextPanel({ sessionId, files, workflow, selectedPreview, onPreviewFile, onClosePreview }: ContextPanelProps) {
  const [previewArtifact, setPreviewArtifact] = useState<GeneratedArtifact | null>(null);

  function handleDocPreview(preview: FilePreview) {
    if (!preview.download_url) return;
    const ext = preview.file_name.split(".").pop()?.toLowerCase() || "";
    const inlineUrl = `${preview.download_url}${preview.download_url.includes("?") ? "&" : "?"}inline=true`;
    setPreviewArtifact({
      type: "file_generated",
      filename: preview.file_name,
      filepath: "",
      download_url: preview.download_url,
      image_url: ext === "pdf" ? inlineUrl : undefined,
    });
  }

  const completedSteps = workflow?.steps?.filter((s) => s.status === "completed").length || 0;
  const totalSteps = workflow?.steps?.length || 0;

  return (
    <div className="w-[320px] h-full bg-panel border-l border-border/30 flex flex-col flex-shrink-0 backdrop-blur-sm">
      <div className="shrink-0 px-4 pt-4 pb-2 flex items-center gap-2 border-b border-border/20">
        <div className="w-1.5 h-1.5 rounded-full bg-sky-400" />
        <span className="text-[11px] font-bold text-muted-foreground/60 tracking-[0.12em] uppercase">
          Runtime
        </span>
      </div>

      <div className="flex-1 min-h-0 flex flex-col border-b border-border/20">
        <div className="shrink-0 px-4 pt-3 pb-1.5">
          <div className="flex items-center gap-2 text-foreground font-semibold text-[12px] tracking-tight">
            <FolderOpen size={13} className="text-muted-foreground/60" strokeWidth={1.8} />
            <h3>上下文文件</h3>
            {files.length > 0 && (
              <span className="ml-auto text-[10px] text-muted-foreground/40 font-mono">{files.length}</span>
            )}
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          <div className="flex flex-col gap-1.5">
            {files.length === 0 ? (
              <div className="text-[11px] text-muted-foreground/40 rounded-lg border border-dashed border-border/40 px-3 py-3 text-center">
                暂无上传文件
              </div>
            ) : (
              files.map((file) => (
                <button key={file.id} onClick={() => onPreviewFile(file.id)} className="flex items-start gap-2.5 p-2 bg-background/60 border border-border/40 rounded-lg text-left hover:border-primary/20 hover:bg-background transition-all duration-150 active:scale-[0.99]">
                  <div className="p-1 bg-primary/5 rounded text-primary/70">
                    <FileText size={13} strokeWidth={1.8} />
                  </div>
                  <div className="flex flex-col min-w-0">
                    <span className="text-[12px] font-medium truncate text-foreground">{file.name}</span>
                    <span className="text-[10px] text-muted-foreground/45 font-mono">{formatFileSize(file.size)}</span>
                  </div>
                </button>
              ))
            )}
          </div>
          {selectedPreview && (
            <div className="mt-2.5 rounded-lg border border-border/40 bg-background/60 overflow-hidden">
              <div className="flex items-center justify-between px-2.5 py-1.5 border-b border-border/25">
                <div className="min-w-0">
                  <div className="text-[12px] font-medium truncate">{selectedPreview.file_name}</div>
                  <div className="text-[10px] text-muted-foreground/45">预览</div>
                </div>
                <div className="flex items-center gap-0.5">
                  {selectedPreview.preview_type === "document" && selectedPreview.download_url && isPreviewable(selectedPreview.file_name) && (
                    <button
                      onClick={() => handleDocPreview(selectedPreview)}
                      className="text-primary hover:bg-primary/5 rounded p-0.5 transition-colors duration-150"
                      title="打开预览"
                    >
                      <Eye size={13} />
                    </button>
                  )}
                  <button onClick={onClosePreview} className="text-muted-foreground/40 hover:text-foreground transition-colors duration-150">
                    <X size={13} />
                  </button>
                </div>
              </div>
              <div className="max-h-56 overflow-auto p-2.5 text-[11px] text-muted-foreground font-mono whitespace-pre-wrap">
                <PreviewContent preview={selectedPreview} onDocPreview={handleDocPreview} />
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 flex flex-col border-b border-border/20">
        <div className="shrink-0 px-4 pt-3 pb-1.5">
          <div className="flex items-center gap-2 text-foreground font-semibold text-[12px] tracking-tight">
            <ListTree size={13} className="text-muted-foreground/60" strokeWidth={1.8} />
            <h3>执行计划</h3>
            {totalSteps > 0 && (
              <span className="ml-auto text-[10px] text-muted-foreground/40 font-mono">{completedSteps}/{totalSteps}</span>
            )}
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          {!workflow?.steps?.length ? (
            <div className="text-[11px] text-muted-foreground/40 rounded-lg border border-dashed border-border/40 px-3 py-3 text-center">
              等待任务规划
            </div>
          ) : (
            <div className="flex flex-col gap-0.5">
              {workflow.steps.map((step, index) => (
                <div key={step.key || `${index}`} className="flex items-center gap-2 py-1">
                  <div className={`w-3 h-3 rounded-full flex items-center justify-center flex-shrink-0 ${
                    step.status === "completed"
                      ? "bg-primary/20 text-primary"
                      : step.status === "running"
                        ? "bg-sky-500/15 text-sky-500"
                        : step.status === "error"
                          ? "bg-red-500/15 text-red-400"
                          : "bg-muted/40 text-muted-foreground/25"
                  }`}>
                    {step.status === "running" ? (
                      <SparkleIcon size={8} className="animate-star-spin-breathe" />
                    ) : step.status === "completed" ? (
                      <svg width="7" height="7" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10" /></svg>
                    ) : step.status === "error" ? (
                      <svg width="7" height="7" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10" /></svg>
                    ) : (
                      <svg width="7" height="7" viewBox="0 0 24 24" fill="currentColor" opacity="0.4"><circle cx="12" cy="12" r="10" /></svg>
                    )}
                  </div>
<span className={`text-[11px] truncate ${
                    step.status === "completed"
                      ? "text-blue-600"
                      : step.status === "error"
                        ? "text-red-500"
                        : "text-foreground"
                  }`}>
                    {step.title || step.label}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 flex flex-col">
        <div className="shrink-0 px-4 pt-3 pb-1.5">
          <div className="flex items-center gap-2 text-foreground font-semibold text-[12px] tracking-tight">
            <Clock size={13} className="text-muted-foreground/60" strokeWidth={1.8} />
            <h3>定时任务</h3>
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          <ScheduledTasksPanel />
        </div>
      </div>

      {previewArtifact && (
        <DocumentPreviewDialog
          artifact={previewArtifact}
          open={!!previewArtifact}
          onOpenChange={(open) => { if (!open) setPreviewArtifact(null); }}
        />
      )}
    </div>
  );
}

function PreviewContent({ preview, onDocPreview }: { preview: FilePreview; onDocPreview: (preview: FilePreview) => void }) {
  if (preview.preview_type === "text" || preview.preview_type === "missing" || preview.preview_type === "unsupported") {
    return <div>{preview.content || "暂无预览"}</div>;
  }

  if (preview.preview_type === "table") {
    return <TablePreview columns={preview.columns || []} rows={preview.rows || []} />;
  }

  if (preview.preview_type === "excel") {
    return (
      <div className="flex flex-col gap-2.5">
        {(preview.sheets || []).map((sheet) => (
          <div key={sheet.sheet_name}>
            <div className="text-[10px] font-semibold text-foreground mb-1">{sheet.sheet_name}</div>
            <TablePreview columns={sheet.columns || []} rows={sheet.rows || []} />
          </div>
        ))}
      </div>
    );
  }

  if (preview.preview_type === "document") {
    return (
      <div className="flex flex-col items-center gap-2.5 py-3">
        <FileText size={28} className="text-muted-foreground/30" />
        <span className="text-muted-foreground/50 text-[11px]">{preview.content || "该文件支持在线预览"}</span>
        {preview.download_url && isPreviewable(preview.file_name) && (
          <button
            onClick={() => onDocPreview(preview)}
            className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] text-primary bg-primary/5 hover:bg-primary/10 transition-colors duration-150"
          >
            <Eye size={12} />
            打开预览
          </button>
        )}
      </div>
    );
  }

  return <div>暂无预览</div>;
}

function TablePreview({ columns, rows }: { columns: string[]; rows: string[][] }) {
  return (
    <div className="overflow-auto rounded-md border border-border/30">
      <table className="min-w-full border-collapse text-[10px]">
        <thead>
          <tr className="bg-muted/20">
            {columns.map((column) => (
              <th key={column} className="border-b border-border/30 px-1.5 py-0.5 text-left font-semibold text-foreground whitespace-nowrap">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${rowIndex}`}>
              {row.map((cell, cellIndex) => (
                <td key={`${rowIndex}-${cellIndex}`} className="border-b border-border/20 px-1.5 py-0.5 whitespace-nowrap text-muted-foreground/70">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
