import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  ListTree,
  X,
  Eye,
  FolderOpen,
  Clock,
  CheckCircle2,
  Circle,
  Loader2,
  XCircle,
} from "lucide-react";
import type { FilePreview, GeneratedArtifact, UploadedFileItem, WorkflowPlan, WorkflowStepItem } from "@/types/app";
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
    <div
      className="w-[320px] h-full flex flex-col flex-shrink-0 overflow-hidden animate-slide-in-right"
      style={{
        background: 'var(--glass-bg)',
        borderLeft: '1px solid hsl(var(--border))',
        backdropFilter: 'blur(12px)',
      }}
    >
      {/* Runtime Header */}
      <div
        className="shrink-0 px-4 pt-4 pb-3 flex items-center gap-2.5"
        style={{ borderBottom: '1px solid hsl(var(--border))' }}
      >
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center animate-glow-pulse"
          style={{ background: 'var(--ocean-50)' }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" style={{ color: 'var(--ocean-500)' }}>
            <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <div>
          <span
            className="text-[10px] font-bold tracking-[0.16em] uppercase"
            style={{ color: 'var(--ocean-400)', fontFamily: 'var(--font-mono)' }}
          >
            Runtime
          </span>
        </div>
        <div className="ml-auto flex items-center gap-1">
          <div className="w-1.5 h-1.5 rounded-full animate-pulse-subtle" style={{ background: 'var(--teal-400)' }} />
          <span className="text-[9px] font-semibold" style={{ color: 'var(--teal-400)', opacity: 0.7 }}>Active</span>
        </div>
      </div>

      {/* Files Section */}
      <div className="flex-1 min-h-0 flex flex-col" style={{ borderBottom: '1px solid hsl(var(--border))' }}>
        <div className="shrink-0 px-4 pt-3 pb-2">
          <div className="flex items-center gap-2 font-semibold text-[12px] tracking-tight" style={{ color: 'hsl(var(--foreground))' }}>
            <FolderOpen size={13} style={{ color: 'var(--ocean-400)' }} strokeWidth={1.8} />
            <h3>上下文文件</h3>
            {files.length > 0 && (
              <span className="ml-auto text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: 'var(--ocean-50)', color: 'var(--ocean-400)' }}>
                {files.length}
              </span>
            )}
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          <div className="flex flex-col gap-1.5">
            {files.length === 0 ? (
              <div className="py-8 flex flex-col items-center gap-2.5">
                <FolderOpen size={32} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.06 }} />
                <span className="text-[11px]" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.2 }}>
                  暂无上传文件
                </span>
              </div>
            ) : (
              files.map((file) => (
                <button
                  key={file.id}
                  onClick={() => onPreviewFile(file.id)}
                  className="group flex items-start gap-2.5 p-2.5 rounded-xl text-left transition-all duration-200 active:scale-[0.99]"
                  style={{
                    background: 'hsl(var(--background))',
                    border: '1px solid hsl(var(--border))',
                    boxShadow: '0 1px 4px rgba(15,31,56,0.02)',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = 'var(--ocean-200)';
                    e.currentTarget.style.background = 'var(--ocean-50)';
                    e.currentTarget.style.boxShadow = '0 2px 8px rgba(15,31,56,0.04)';
                    e.currentTarget.style.transform = 'translateY(-1px)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = 'hsl(var(--border))';
                    e.currentTarget.style.background = 'hsl(var(--background))';
                    e.currentTarget.style.boxShadow = '0 1px 4px rgba(15,31,56,0.02)';
                    e.currentTarget.style.transform = 'translateY(0)';
                  }}
                >
                  <div
                    className="p-1 rounded-lg flex-shrink-0"
                    style={{ background: 'var(--ocean-50)', color: 'var(--ocean-400)' }}
                  >
                    <FileText size={13} strokeWidth={1.8} />
                  </div>
                  <div className="flex flex-col min-w-0">
                    <span className="text-[12px] font-medium truncate" style={{ color: 'hsl(var(--foreground))' }}>
                      {file.name}
                    </span>
                    <span className="text-[10px] font-mono" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35 }}>
                      {formatFileSize(file.size)}
                    </span>
                  </div>
                </button>
              ))
            )}
          </div>
          {selectedPreview && (
            <div
              className="mt-3 rounded-xl overflow-hidden animate-scale-in"
              style={{
                background: 'hsl(var(--background))',
                border: '1px solid hsl(var(--border))',
                boxShadow: '0 2px 8px rgba(15,31,56,0.04)',
              }}
            >
              <div
                className="flex items-center justify-between px-3 py-2.5"
                style={{ borderBottom: '1px solid hsl(var(--border))' }}
              >
                <div className="min-w-0">
                  <div className="text-[12px] font-medium truncate" style={{ color: 'hsl(var(--foreground))' }}>
                    {selectedPreview.file_name}
                  </div>
                  <div className="text-[10px]" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35 }}>
                    预览
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  {selectedPreview.preview_type === "document" && selectedPreview.download_url && isPreviewable(selectedPreview.file_name) && (
                    <button
                      onClick={() => handleDocPreview(selectedPreview)}
                      className="rounded-lg p-1 transition-all duration-150"
                      style={{ color: 'var(--ocean-400)' }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ocean-50)'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                      title="打开预览"
                    >
                      <Eye size={13} />
                    </button>
                  )}
                  <button
                    onClick={onClosePreview}
                    className="rounded-lg p-1 transition-all duration-150"
                    style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35 }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = 'hsl(var(--foreground))'; e.currentTarget.style.opacity = '1'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = 'hsl(var(--muted-foreground))'; e.currentTarget.style.opacity = '0.35'; }}
                  >
                    <X size={13} />
                  </button>
                </div>
              </div>
              <div
                className="max-h-56 overflow-auto p-2.5 text-[11px] font-mono whitespace-pre-wrap"
                style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.65 }}
              >
                <PreviewContent preview={selectedPreview} onDocPreview={handleDocPreview} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Workflow Section */}
      <div className="flex-1 min-h-0 flex flex-col" style={{ borderBottom: '1px solid hsl(var(--border))' }}>
        <div className="shrink-0 px-4 pt-3 pb-2">
          <div className="flex items-center gap-2 font-semibold text-[12px] tracking-tight" style={{ color: 'hsl(var(--foreground))' }}>
            <ListTree size={13} style={{ color: 'var(--teal-400)' }} strokeWidth={1.8} />
            <h3>执行计划</h3>
            {totalSteps > 0 && (
              <div className="ml-auto flex items-center gap-1">
                <div className="h-1 w-16 rounded-full overflow-hidden" style={{ background: 'hsl(var(--muted))' }}>
                  <div className="h-full rounded-full transition-all duration-500" style={{ width: `${(completedSteps / totalSteps) * 100}%`, background: 'var(--gradient-ocean-teal)' }} />
                </div>
                <span className="text-[10px] font-bold font-mono" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35 }}>
                  {completedSteps}/{totalSteps}
                </span>
              </div>
            )}
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          {!workflow?.steps?.length ? (
            <div className="py-8 flex flex-col items-center gap-2.5">
              <ListTree size={32} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.06 }} />
              <span className="text-[11px]" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.2 }}>
                等待任务规划
              </span>
            </div>
          ) : (
            <div className="flex flex-col gap-0.5 stagger-children">
              {workflow.steps.map((step, index) => (
                <div key={step.key || `${index}`} className="flex items-center gap-2.5 py-2 px-2.5 rounded-lg transition-colors duration-200"
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'hsl(var(--muted))'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                >
                  <div
                    className="w-3.5 h-3.5 rounded-full flex items-center justify-center flex-shrink-0"
                    style={{
                      background: step.status === "completed"
                        ? 'var(--teal-50)'
                        : step.status === "running"
                          ? 'var(--ocean-50)'
                          : step.status === "error"
                            ? '#fef2f2'
                            : 'hsl(var(--muted))',
                      color: step.status === "completed"
                        ? 'var(--teal-500)'
                        : step.status === "running"
                          ? 'var(--ocean-500)'
                          : step.status === "error"
                            ? 'hsl(var(--destructive))'
                            : 'hsl(var(--muted-foreground))',
                    }}
                  >
                    {step.status === "running" ? (
                      <SparkleIcon size={8} className="animate-star-spin-breathe" />
                    ) : step.status === "completed" ? (
                      <svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z" /></svg>
                    ) : step.status === "error" ? (
                      <svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10" /></svg>
                    ) : (
                      <svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor" opacity="0.3"><circle cx="12" cy="12" r="10" /></svg>
                    )}
                  </div>
                  <span
                    className="text-[11px] truncate font-medium"
                    style={{
                      color: step.status === "completed"
                        ? 'var(--teal-600)'
                        : step.status === "error"
                          ? 'hsl(var(--destructive))'
                          : step.status === "running"
                            ? 'hsl(var(--foreground))'
                            : 'hsl(var(--muted-foreground))',
                      opacity: step.status === "pending" ? 0.45 : 1,
                    }}
                  >
                    {step.title || step.label}
                  </span>
                  {step.status === "running" && (
                    <span className="ml-auto text-[9px] font-semibold animate-pulse-subtle" style={{ color: 'var(--ocean-400)', opacity: 0.5 }}>
                      执行中
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Scheduled Tasks Section */}
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="shrink-0 px-4 pt-3 pb-2">
          <div className="flex items-center gap-2 font-semibold text-[12px] tracking-tight" style={{ color: 'hsl(var(--foreground))' }}>
            <Clock size={13} style={{ color: 'var(--amber-500)' }} strokeWidth={1.8} />
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
      <div className="flex flex-col gap-3">
        {(preview.sheets || []).map((sheet) => (
          <div key={sheet.sheet_name}>
            <div className="text-[10px] font-bold mb-1.5" style={{ color: 'hsl(var(--foreground))', opacity: 0.7 }}>
              {sheet.sheet_name}
            </div>
            <TablePreview columns={sheet.columns || []} rows={sheet.rows || []} />
          </div>
        ))}
      </div>
    );
  }

  if (preview.preview_type === "document") {
    return (
      <div className="flex flex-col items-center gap-3 py-4">
        <FileText size={32} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.08 }} />
        <span style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.4 }} className="text-[11px]">
          {preview.content || "该文件支持在线预览"}
        </span>
        {preview.download_url && isPreviewable(preview.file_name) && (
          <button
            onClick={() => onDocPreview(preview)}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px] font-medium transition-all duration-200"
            style={{ color: 'var(--ocean-500)', background: 'var(--ocean-50)' }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ocean-100)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--ocean-50)'; }}
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
    <div className="overflow-auto rounded-lg" style={{ border: '1px solid hsl(var(--border))' }}>
      <table className="min-w-full border-collapse text-[10px]">
        <thead>
          <tr style={{ background: 'hsl(var(--muted))' }}>
            {columns.map((column) => (
              <th
                key={column}
                className="px-1.5 py-1 text-left font-semibold whitespace-nowrap"
                style={{ borderBottom: '1px solid hsl(var(--border))', color: 'hsl(var(--foreground))', opacity: 0.8 }}
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${rowIndex}`} className="transition-colors duration-150"
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ocean-50)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
            >
              {row.map((cell, cellIndex) => (
                <td
                  key={`${rowIndex}-${cellIndex}`}
                  className="px-1.5 py-0.5 whitespace-nowrap"
                  style={{ borderBottom: '1px solid hsl(var(--border))', color: 'hsl(var(--muted-foreground))', opacity: 0.6 }}
                >
                  {cell}
                </td>
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