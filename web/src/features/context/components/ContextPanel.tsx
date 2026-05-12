import { useState } from "react";
import { FileText, ListTree, Paperclip, X, Eye, FolderOpen, Clock } from "lucide-react";
import type { FilePreview, GeneratedArtifact, UploadedFileItem, WorkflowPlan } from "@/types/app";
import { isPreviewable } from "@/features/chat/components/DocumentPreviewDialog";
import { DocumentPreviewDialog } from "@/features/chat/components/DocumentPreviewDialog";
import { ScheduledTasksPanel } from "@/features/scheduler/components/ScheduledTasksPanel";

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
        <div className="w-1.5 h-1.5 rounded-full bg-primary/60" />
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
            <div className="relative ml-2 flex flex-col gap-0">
              {workflow.steps.map((step, index) => {
                const isLast = index === workflow.steps.length - 1;
                return (
                  <div key={step.key || `${index}`} className="relative pl-6 pb-3">
                    {!isLast && (
                      <div className={`absolute left-[5px] top-[14px] bottom-0 w-px ${step.status === "completed" ? "bg-primary/20" : "bg-border/30"}`} />
                    )}
                    <div className={`absolute left-0 top-[5px] w-[11px] h-[11px] rounded-full border-2 border-background ${
                      step.status === "completed"
                        ? "bg-primary border-primary/30"
                        : step.status === "running"
                          ? "bg-background border-primary"
                          : "bg-muted border-border/50"
                    }`}>
                      {step.status === "running" && (
                        <div className="w-[5px] h-[5px] bg-primary rounded-full animate-pulse-subtle mx-auto mt-[0.5px]" />
                      )}
                    </div>
                    <div className="flex flex-col">
                      <div className="flex items-center gap-1.5">
                        <span className={`text-[10px] font-mono ${step.status === "pending" ? "text-muted-foreground/35" : "text-muted-foreground/60"}`}>
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <span className={`text-[12px] font-medium ${step.status === "pending" ? "text-muted-foreground/45" : "text-foreground"}`}>
                          {step.title || step.label}
                        </span>
                      </div>
                      {step.detail ? <span className="text-[10px] mt-0.5 text-muted-foreground/50 whitespace-pre-wrap break-words">{step.detail}</span> : null}
                      {step.outcome ? <span className="text-[10px] mt-0.5 text-primary/70 whitespace-pre-wrap break-words">{step.outcome}</span> : null}
                      {step.expected_deliverables?.length ? (
                        <span className="text-[9px] mt-0.5 text-muted-foreground/30">交付: {step.expected_deliverables.map((d) => d.type).join(", ")}</span>
                      ) : null}
                      {step.output_artifacts?.length ? (
                        <span className="text-[9px] mt-0.5 text-primary/40">产物: {step.output_artifacts.map((a) => a.split("/").pop() || a).join(", ")}</span>
                      ) : null}
                    </div>
                  </div>
                );
              })}
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
