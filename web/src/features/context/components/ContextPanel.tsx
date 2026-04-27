import { useState } from "react";
import { FileText, ListTree, Paperclip, X, Eye } from "lucide-react";
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

  return (
    <div className="w-[340px] h-full bg-card/60 border-l border-border/60 flex flex-col flex-shrink-0 backdrop-blur-sm">
      {/* 上下文文件 */}
      <div className="flex-1 min-h-0 flex flex-col border-b border-border/40">
        <div className="shrink-0 px-5 pt-5 pb-2">
          <div className="flex items-center gap-2 text-foreground font-medium text-[13px] tracking-tight">
            <Paperclip size={16} className="text-muted-foreground" />
            <h3>上下文文件</h3>
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-5 pb-4">
          <div className="flex flex-col gap-2">
            {files.length === 0 ? (
              <div className="text-sm text-muted-foreground/60 rounded-xl border border-dashed border-border/60 px-3 py-4 text-center">暂无上传文件</div>
            ) : (
              files.map((file) => (
                <button key={file.id} onClick={() => onPreviewFile(file.id)} className="flex items-start gap-3 p-2.5 bg-background border border-border/60 rounded-xl text-left hover:border-primary/25 transition-all duration-150 active:scale-[0.98]">
                  <div className="p-1.5 bg-primary/6 rounded-lg text-primary">
                    <FileText size={15} />
                  </div>
                  <div className="flex flex-col min-w-0">
                    <span className="text-[13px] font-medium truncate text-foreground">{file.name}</span>
                    <span className="text-[11px] text-muted-foreground/60">{formatFileSize(file.size)}</span>
                  </div>
                </button>
              ))
            )}
          </div>
          {selectedPreview && (
            <div className="mt-3 rounded-xl border border-border/60 bg-background overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 border-b border-border/40">
                <div className="min-w-0">
                  <div className="text-[13px] font-medium truncate">{selectedPreview.file_name}</div>
                  <div className="text-[11px] text-muted-foreground/60">预览</div>
                </div>
                <div className="flex items-center gap-1">
                  {selectedPreview.preview_type === "document" && selectedPreview.download_url && isPreviewable(selectedPreview.file_name) && (
                    <button
                      onClick={() => handleDocPreview(selectedPreview)}
                      className="text-primary hover:bg-primary/5 rounded-md p-1 transition-colors duration-150"
                      title="打开预览"
                    >
                      <Eye size={14} />
                    </button>
                  )}
                  <button onClick={onClosePreview} className="text-muted-foreground/60 hover:text-foreground transition-colors duration-150">
                    <X size={14} />
                  </button>
                </div>
              </div>
              <div className="max-h-64 overflow-auto p-3 text-xs text-muted-foreground font-mono whitespace-pre-wrap">
                <PreviewContent preview={selectedPreview} onDocPreview={handleDocPreview} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 执行计划 */}
      <div className="flex-1 min-h-0 flex flex-col border-b border-border/40">
        <div className="shrink-0 px-5 pt-5 pb-2">
          <div className="flex items-center gap-2 text-foreground font-medium text-[13px] tracking-tight">
            <ListTree size={16} className="text-muted-foreground" />
            <h3>执行计划</h3>
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-5 pb-4">
          {!workflow?.steps?.length ? (
            <div className="text-sm text-muted-foreground/60 rounded-xl border border-dashed border-border/60 px-3 py-4 text-center">等待工作流规划</div>
          ) : (
            <div className="relative border-l-2 border-border/40 ml-3 flex flex-col gap-4 pr-2">
              {workflow.steps.map((step, index) => (
                <div key={step.key || `${index}`} className="relative pl-6">
                  <div className={`absolute -left-[9px] top-1 w-4 h-4 rounded-full border-2 border-background ${step.status === "completed" ? "bg-primary" : step.status === "running" ? "bg-background border-primary" : "bg-muted border-muted-foreground/30"}`}>
                    {step.status === "running" && <div className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse mx-auto mt-[3px]" />}
                  </div>
                  <div className="flex flex-col">
                    <span className={`text-[13px] font-medium ${step.status === "pending" ? "text-muted-foreground/60" : "text-foreground"}`}>步骤 {index + 1}</span>
                    <span className={`text-xs mt-0.5 font-medium ${step.status === "pending" ? "text-muted-foreground/50" : "text-foreground"}`}>{step.title || step.label}</span>
                    {step.detail ? <span className="text-[11px] mt-1 text-muted-foreground/70 whitespace-pre-wrap break-words">{step.detail}</span> : null}
                    {step.outcome ? <span className="text-[11px] mt-1 text-primary whitespace-pre-wrap break-words">{step.outcome}</span> : null}
                    {step.expected_deliverables?.length ? <span className="text-[10px] mt-1 text-muted-foreground/40">交付物: {step.expected_deliverables.map((d) => d.type).join(", ")}</span> : null}
                    {step.output_artifacts?.length ? <span className="text-[10px] mt-1 text-primary/60">产物: {step.output_artifacts.map((a) => a.split("/").pop() || a).join(", ")}</span> : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 定时任务 */}
      <div className="flex-1 min-h-0 flex flex-col">
        <ScheduledTasksPanel />
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
      <div className="flex flex-col gap-3">
        {(preview.sheets || []).map((sheet) => (
          <div key={sheet.sheet_name}>
            <div className="text-[11px] font-semibold text-foreground mb-1">{sheet.sheet_name}</div>
            <TablePreview columns={sheet.columns || []} rows={sheet.rows || []} />
          </div>
        ))}
      </div>
    );
  }

  if (preview.preview_type === "document") {
    return (
      <div className="flex flex-col items-center gap-3 py-4">
        <FileText size={32} className="text-muted-foreground/40" />
        <span className="text-muted-foreground/60">{preview.content || "该文件支持在线预览"}</span>
        {preview.download_url && isPreviewable(preview.file_name) && (
          <button
            onClick={() => onDocPreview(preview)}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-primary bg-primary/5 hover:bg-primary/10 transition-colors duration-150"
          >
            <Eye size={13} />
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
    <div className="overflow-auto rounded-lg border border-border/40">
      <table className="min-w-full border-collapse text-[11px]">
        <thead>
          <tr className="bg-muted/30">
            {columns.map((column) => (
              <th key={column} className="border-b border-border/40 px-2 py-1 text-left font-semibold text-foreground whitespace-nowrap">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${rowIndex}`}>
              {row.map((cell, cellIndex) => (
                <td key={`${rowIndex}-${cellIndex}`} className="border-b border-border/30 px-2 py-1 whitespace-nowrap text-muted-foreground">{cell}</td>
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
