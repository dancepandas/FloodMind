import { useCallback, useEffect, useState } from "react";
import { AlarmClock, Eye, RefreshCw, Trash2 } from "lucide-react";
import { deleteScheduledTask, fetchScheduledTasks } from "@/api/agent";
import type { ScheduledTask } from "@/types/app";
import { ScheduledTaskResultDialog } from "./ScheduledTaskResultDialog";

export function ScheduledTasksPanel() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refreshTasks = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const items = await fetchScheduledTasks();
      setTasks(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "获取定时任务失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshTasks();
    const timer = window.setInterval(refreshTasks, 30000);
    return () => window.clearInterval(timer);
  }, [refreshTasks]);

  async function handleDelete(taskId: string) {
    if (!window.confirm("确认删除这个定时任务？")) return;
    await deleteScheduledTask(taskId);
    await refreshTasks();
  }

  function handleViewResult(task: ScheduledTask) {
    setSelectedTask(task);
  }

  const [selectedTask, setSelectedTask] = useState<ScheduledTask | null>(null);

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="shrink-0 px-5 pt-5 pb-2">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-foreground font-medium text-[13px] tracking-tight">
            <AlarmClock size={16} className="text-muted-foreground" />
            <h3>定时任务</h3>
          </div>
          <button
            onClick={refreshTasks}
            className="text-muted-foreground/50 hover:text-primary transition-colors duration-150 disabled:opacity-40"
            disabled={loading}
            title="刷新定时任务"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-5 pb-4">
        <div className="mb-3 rounded-lg border border-primary/10 bg-primary/[0.03] px-3 py-2 text-[11px] leading-relaxed text-muted-foreground/70">
          在聊天框描述即可创建，例如：每天早上8点运行荆州水文模型并生成Excel报告。
        </div>

        {error ? <div className="mb-2 rounded-lg border border-destructive/15 bg-destructive/[0.04] px-3 py-2 text-xs text-destructive">{error}</div> : null}

        {tasks.length === 0 ? (
          <div className="text-sm text-muted-foreground/60 rounded-xl border border-dashed border-border/60 px-3 py-4 text-center">
            暂无定时任务
          </div>
        ) : (
          <div className="flex flex-col gap-2.5">
            {tasks.map((task) => (
              <TaskCard key={task.id} task={task} onDelete={handleDelete} onViewResult={handleViewResult} />
            ))}
          </div>
        )}
      </div>

      <ScheduledTaskResultDialog
        task={selectedTask}
        open={!!selectedTask}
        onOpenChange={(open) => { if (!open) setSelectedTask(null); }}
      />
    </div>
  );
}

function TaskCard({ task, onDelete, onViewResult }: { task: ScheduledTask; onDelete: (taskId: string) => void; onViewResult: (task: ScheduledTask) => void }) {
  const hasResult = !!task.last_result || !!task.last_error || (task.artifacts || []).length > 0;
  return (
    <div className="rounded-xl border border-border/60 bg-background hover:border-border transition-colors duration-150 overflow-hidden">
      <div className="p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <StatusBadge task={task} />
              <span className="text-[11px] text-muted-foreground/50">{task.repeat === "daily" ? "每天重复" : "一次性"}</span>
            </div>
            <div className="text-[13px] font-medium text-foreground break-words leading-snug">{task.command}</div>
          </div>
          <div className="flex items-center gap-0.5 shrink-0">
            {hasResult && (
              <button
                onClick={() => onViewResult(task)}
                className="text-muted-foreground/50 hover:text-primary transition-colors duration-150 p-1 rounded-md hover:bg-primary/5"
                title="查看结果"
              >
                <Eye size={14} />
              </button>
            )}
            <button
              onClick={() => onDelete(task.id)}
              className="text-muted-foreground/40 hover:text-destructive transition-colors duration-150 p-1 rounded-md hover:bg-destructive/5"
              title="删除任务"
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>

        <div className="mt-2.5 grid grid-cols-1 gap-1 text-[11px] text-muted-foreground/60">
          <InfoLine label="所属会话" value={task.session_id.length > 12 ? task.session_id.slice(0, 12) + "…" : task.session_id} />
          <InfoLine label="下次执行" value={formatDateTime(task.next_run_at)} />
          <InfoLine label="最近执行" value={formatDateTime(task.last_run_at) || "尚未执行"} />
          {task.last_error ? <InfoLine label="错误" value={task.last_error!.length > 60 ? task.last_error!.slice(0, 60) + "…" : task.last_error} danger /> : null}
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ task }: { task: ScheduledTask }) {
  const status = task.status === "running" ? "running" : task.last_status || task.status;
  const labelMap: Record<string, string> = {
    pending: "等待中",
    running: "执行中",
    completed: "已完成",
    failed: "失败",
    disabled: "已停用",
    missed: "已跳过",
  };
  const tone = status === "running" ? "bg-primary/8 text-primary" : status === "failed" || status === "missed" ? "bg-destructive/6 text-destructive" : status === "completed" ? "bg-emerald-500/8 text-emerald-700" : "bg-muted text-muted-foreground/60";
  return <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${tone}`}>{labelMap[status] || status}</span>;
}

function InfoLine({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className="flex gap-2">
      <span className="shrink-0 text-muted-foreground/40">{label}</span>
      <span className={`min-w-0 break-words ${danger ? "text-destructive/80" : "text-muted-foreground/70"}`}>{value}</span>
    </div>
  );
}

function formatDateTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
