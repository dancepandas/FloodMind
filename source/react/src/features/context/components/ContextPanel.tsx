import { FileText, ListTree, Loader2, Paperclip, Wrench, X } from "lucide-react";
import type { FilePreview, ToolActivity, UploadedFileItem, WorkflowPlan } from "@/types/app";

interface ContextPanelProps {
  files: UploadedFileItem[];
  tools: ToolActivity[];
  workflow?: WorkflowPlan | null;
  selectedPreview?: FilePreview | null;
  onPreviewFile: (fileId: string) => void;
  onClosePreview: () => void;
}

export function ContextPanel({ files, tools, workflow, selectedPreview, onPreviewFile, onClosePreview }: ContextPanelProps) {
  return (
    <div className="w-[340px] h-full bg-[rgba(246,250,255,0.92)] border-l border-border flex flex-col flex-shrink-0 overflow-y-auto backdrop-blur-sm">
      <div className="p-4 border-b border-border">
        <div className="flex items-center gap-2 mb-3 text-foreground font-medium">
          <Paperclip size={18} />
          <h3>上下文文件</h3>
        </div>
        <div className="flex flex-col gap-2">
          {files.length === 0 ? (
            <div className="text-sm text-muted-foreground rounded-lg border border-dashed border-border px-3 py-4 text-center">暂无上传文件</div>
          ) : (
            files.map((file) => (
              <button key={file.id} onClick={() => onPreviewFile(file.id)} className="flex items-start gap-3 p-2.5 bg-background border border-border rounded-lg shadow-sm text-left hover:border-primary/40 transition-colors">
                <div className="p-1.5 bg-muted rounded text-primary">
                  <FileText size={16} />
                </div>
                <div className="flex flex-col min-w-0">
                  <span className="text-sm font-medium truncate text-foreground">{file.name}</span>
                  <span className="text-xs text-muted-foreground">{formatFileSize(file.size)}</span>
                </div>
              </button>
            ))
          )}
        </div>
        {selectedPreview && (
          <div className="mt-3 rounded-xl border border-border bg-background shadow-sm overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 border-b border-border">
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">{selectedPreview.file_name}</div>
                <div className="text-[11px] text-muted-foreground">预览</div>
              </div>
              <button onClick={onClosePreview} className="text-muted-foreground hover:text-foreground">
                <X size={14} />
              </button>
            </div>
            <div className="max-h-64 overflow-auto p-3 text-xs text-muted-foreground font-mono whitespace-pre-wrap">
              <PreviewContent preview={selectedPreview} />
            </div>
          </div>
        )}
      </div>

      <div className="p-4 border-b border-border">
        <div className="flex items-center gap-2 mb-3 text-foreground font-medium">
          <Wrench size={18} />
          <h3>工具执行</h3>
        </div>
        <div className="flex max-h-[280px] flex-col gap-2 overflow-y-auto pr-1">
          {tools.length === 0 ? (
            <div className="text-sm text-muted-foreground rounded-lg border border-dashed border-border px-3 py-4 text-center">工具过程会显示在这里</div>
          ) : (
            tools.map((tool) => (
              <div key={tool.id} className="p-3 bg-background border border-border rounded-lg shadow-sm flex flex-col gap-2 min-h-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-foreground truncate">{tool.toolName}</span>
                  {tool.status === "running" && <Loader2 size={14} className="text-primary animate-spin" />}
                  {tool.status === "done" && <span className="text-[11px] text-primary font-semibold">完成</span>}
                  {tool.status === "error" && <span className="text-[11px] text-destructive font-semibold">失败</span>}
                </div>
                <div className="max-h-28 overflow-y-auto text-xs bg-muted/50 p-2 rounded text-muted-foreground font-mono whitespace-pre-wrap break-all">
                  {tool.content}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="p-4">
        <div className="flex items-center gap-2 mb-4 text-foreground font-medium">
          <ListTree size={18} />
          <h3>执行计划</h3>
        </div>
        {!workflow?.steps?.length ? (
          <div className="text-sm text-muted-foreground rounded-lg border border-dashed border-border px-3 py-4 text-center">等待工作流规划</div>
        ) : (
          <div className="relative border-l-2 border-muted ml-3 flex max-h-[320px] flex-col gap-4 overflow-y-auto pr-2">
            {workflow.steps.map((step, index) => (
              <div key={step.key || `${index}`} className="relative pl-6">
                <div className={`absolute -left-[9px] top-1 w-4 h-4 rounded-full border-2 border-background ${step.status === "completed" ? "bg-primary" : step.status === "running" ? "bg-background border-primary" : "bg-muted border-muted-foreground"}`}>
                  {step.status === "running" && <div className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse mx-auto mt-[3px]" />}
                </div>
                <div className="flex flex-col">
                  <span className={`text-sm font-medium ${step.status === "pending" ? "text-muted-foreground" : "text-foreground"}`}>步骤 {index + 1}</span>
                  <span className={`text-xs mt-0.5 font-medium ${step.status === "pending" ? "text-muted-foreground/70" : "text-foreground"}`}>{step.title || step.label}</span>
                  {step.detail ? <span className="text-[11px] mt-1 text-muted-foreground whitespace-pre-wrap break-words">{step.detail}</span> : null}
                  {step.outcome ? <span className="text-[11px] mt-1 text-primary whitespace-pre-wrap break-words">{step.outcome}</span> : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PreviewContent({ preview }: { preview: FilePreview }) {
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

  return <div>暂无预览</div>;
}

function TablePreview({ columns, rows }: { columns: string[]; rows: string[][] }) {
  return (
    <div className="overflow-auto rounded-lg border border-border">
      <table className="min-w-full border-collapse text-[11px]">
        <thead>
          <tr className="bg-muted/50">
            {columns.map((column) => (
              <th key={column} className="border-b border-border px-2 py-1 text-left font-semibold text-foreground whitespace-nowrap">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${rowIndex}`}>
              {row.map((cell, cellIndex) => (
                <td key={`${rowIndex}-${cellIndex}`} className="border-b border-border px-2 py-1 whitespace-nowrap">{cell}</td>
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
