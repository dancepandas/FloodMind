import { AlarmClock, Download, FileText } from "lucide-react";
import { buildApiUrl } from "@/api/client";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import type { ScheduledTask } from "@/types/app";

interface ScheduledTaskResultDialogProps {
  task: ScheduledTask | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ScheduledTaskResultDialog({ task, open, onOpenChange }: ScheduledTaskResultDialogProps) {
  if (!task) return null;

  const artifacts = task.artifacts || [];
  const hasResult = !!task.last_result;
  const hasError = !!task.last_error;
  const executed = !!task.last_run_at;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlarmClock size={20} className="text-primary" />
            定时任务结果
          </DialogTitle>
          <DialogDescription className="truncate">{task.command}</DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto -mx-6 px-6 space-y-4 pb-2">
          <StatusSection task={task} />

          <TimeSection task={task} />

          {hasError && (
            <div className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2">
              <div className="text-[11px] font-medium text-destructive mb-1">错误信息</div>
              <div className="text-xs text-destructive/90 whitespace-pre-wrap break-words leading-relaxed">{task.last_error}</div>
            </div>
          )}

          <div className="rounded-lg border border-border">
            <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border bg-muted/30">
              <FileText size={14} className="text-muted-foreground" />
              <span className="text-xs font-medium text-muted-foreground">执行结果</span>
            </div>
            <div className="max-h-[280px] overflow-y-auto px-3 py-2">
              {hasResult ? (
                <pre className="text-xs text-foreground whitespace-pre-wrap break-words leading-relaxed font-sans">{task.last_result}</pre>
              ) : executed ? (
                <div className="text-xs text-muted-foreground py-2">本次执行未返回文本结果</div>
              ) : (
                <div className="text-xs text-muted-foreground py-2">任务尚未执行</div>
              )}
            </div>
          </div>

          <div className="rounded-lg border border-border">
            <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-muted/30">
              <div className="flex items-center gap-1.5">
                <Download size={14} className="text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">本次新增文件</span>
              </div>
              {artifacts.length > 0 && (
                <span className="text-[10px] text-muted-foreground/75">{artifacts.length} 个文件</span>
              )}
            </div>
            <div className="px-3 py-2">
              {artifacts.length === 0 ? (
                <div className="text-xs text-muted-foreground/75 py-1">
                  {executed ? "本次执行未生成文件" : "任务尚未执行，暂无文件"}
                </div>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {artifacts.map((artifact) => (
                    <a
                      key={artifact.filename}
                      href={buildApiUrl(artifact.download_url)}
                      target="_blank"
                      rel="noreferrer"
                      className="flex items-center justify-between gap-2 rounded-md border border-border bg-background px-2.5 py-2 text-xs text-foreground hover:border-primary/40 hover:text-primary transition-colors"
                    >
                      <span className="truncate">{artifact.filename}</span>
                      <span className="flex items-center gap-1.5 shrink-0 text-muted-foreground">
                        {formatFileSize(artifact.size || 0)}
                        <Download size={12} />
                      </span>
                    </a>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function StatusSection({ task }: { task: ScheduledTask }) {
  const status = task.status === "running" ? "running" : task.last_status || task.status;
  const labelMap: Record<string, string> = {
    pending: "等待中",
    running: "执行中",
    completed: "已完成",
    failed: "失败",
    disabled: "已停用",
    missed: "已跳过",
  };
  const tone =
    status === "running"
      ? "bg-primary/10 text-primary"
      : status === "failed" || status === "missed"
        ? "bg-destructive/10 text-destructive"
        : status === "completed"
          ? "bg-emerald-500/10 text-emerald-700"
          : "bg-muted text-muted-foreground";

  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-muted-foreground/75">状态</span>
      <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium ${tone}`}>
        {labelMap[status] || status}
      </span>
      <span className="text-[11px] text-muted-foreground">
        {task.repeat === "daily" ? "每天重复" : "一次性任务"}
      </span>
    </div>
  );
}

function TimeSection({ task }: { task: ScheduledTask }) {
  const items: { label: string; value: string }[] = [];
  if (task.last_run_at) items.push({ label: "最近执行", value: formatDateTime(task.last_run_at) });
  if (task.last_finished_at) items.push({ label: "完成时间", value: formatDateTime(task.last_finished_at) });
  if (task.next_run_at) items.push({ label: "下次执行", value: formatDateTime(task.next_run_at) });

  if (items.length === 0) return null;

  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
      {items.map((item) => (
        <div key={item.label} className="flex gap-2">
          <span className="shrink-0 text-muted-foreground/75">{item.label}</span>
          <span className="text-muted-foreground">{item.value}</span>
        </div>
      ))}
    </div>
  );
}

function formatDateTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatFileSize(bytes: number) {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
