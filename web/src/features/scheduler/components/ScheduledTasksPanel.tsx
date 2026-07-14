import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { AlarmClock, Eye, RefreshCw, Trash2 } from "lucide-react";
import { deleteScheduledTask, fetchScheduledTasks } from "@/api/agent";
import type { ScheduledTask } from "@/types/app";
import { ScheduledTaskResultDialog } from "./ScheduledTaskResultDialog";
import { ErrorMessage } from "@/features/chat/components/ErrorMessage";

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
          <div className="flex items-center gap-2 font-medium text-[13px] tracking-tight" style={{ color: 'var(--text-primary)' }}>
            <AlarmClock size={16} style={{ color: 'var(--sand)' }} strokeWidth={1.8} />
            <h3>定时任务</h3>
          </div>
          <button
            onClick={refreshTasks}
            className="transition-colors duration-150 disabled:opacity-40"
            style={{ color: 'var(--text-tertiary)' }}
            disabled={loading}
            title="刷新定时任务"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-5 pb-4">
        <div
          className="mb-3 rounded-xl px-3 py-2 text-[11px] leading-relaxed"
          style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
        >
          在聊天框描述即可创建，例如：每天早上8点运行荆州水文模型并生成Excel报告。
        </div>

        {error ? <ErrorMessage title="获取失败" message={error} /> : null}

        {tasks.length === 0 ? (
          <div
            className="text-sm text-center px-3 py-4 rounded-xl border border-dashed"
            style={{ color: 'var(--text-tertiary)', borderColor: 'var(--border)' }}
          >
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
    <div
      className="rounded-xl overflow-hidden transition-colors duration-200"
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        boxShadow: '0 1px 3px -1px rgba(15,23,42,0.03)',
      }}
    >
      <div className="p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <StatusBadge task={task} />
              <span className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>{task.repeat === "daily" ? "每天重复" : "一次性"}</span>
            </div>
            <div className="text-[13px] font-medium break-words leading-snug" style={{ color: 'var(--text-primary)' }}>{task.command}</div>
          </div>
          <div className="flex items-center gap-0.5 shrink-0">
            {hasResult && (
              <button
                onClick={() => onViewResult(task)}
                className="p-1 rounded-md transition-colors duration-150"
                style={{ color: 'var(--text-tertiary)' }}
                title="查看结果"
              >
                <Eye size={14} />
              </button>
            )}
            <button
              onClick={() => onDelete(task.id)}
              className="p-1 rounded-md transition-colors duration-150"
              style={{ color: 'var(--text-tertiary)' }}
              title="删除任务"
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>

        <div className="mt-2.5 grid grid-cols-1 gap-1 text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
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
  const tones: Record<string, CSSProperties> = {
    running: { background: 'rgba(14,165,233,0.10)', color: 'var(--wave)' },
    failed: { background: 'rgba(244,63,94,0.08)', color: 'var(--alert)' },
    missed: { background: 'rgba(244,63,94,0.08)', color: 'var(--alert)' },
    completed: { background: 'rgba(20,184,166,0.10)', color: 'var(--reef)' },
  };
  const tone = tones[status] || { background: 'var(--surface-2)', color: 'var(--text-tertiary)' };
  return (
    <span className="rounded-md px-1.5 py-0.5 text-[10px] font-semibold" style={tone}>
      {labelMap[status] || status}
    </span>
  );
}

function InfoLine({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className="flex gap-2">
      <span className="shrink-0" style={{ color: 'var(--text-tertiary)', opacity: 0.7 }}>{label}</span>
      <span className="min-w-0 break-words" style={{ color: danger ? 'var(--alert)' : 'var(--text-secondary)' }}>{value}</span>
    </div>
  );
}

function formatDateTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
