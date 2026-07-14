import { MessageSquare, Plus, Trash2, Clock } from "lucide-react";
import type { SessionSummary } from "@/types/app";

interface SidebarProps {
  sessions: SessionSummary[];
  activeSessionId: string;
  onNewSession: () => void;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onShowScheduledTasks: () => void;
}

function BrandIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-5 h-5" fill="currentColor">
      <path d="M12 2C8.5 2 6 5 6 8c0 2.5 1.5 4.5 3 6s3 4 3 8c0-4 1.5-6 3-8s3-3.5 3-6c0-3-2.5-6-6-6zm0 8a2 2 0 1 1 0-4 2 2 0 0 1 0 4z" />
    </svg>
  );
}

export function Sidebar({
  sessions,
  activeSessionId,
  onNewSession,
  onSelectSession,
  onDeleteSession,
  onShowScheduledTasks,
}: SidebarProps) {
  return (
    <div
      className="w-[280px] h-full flex flex-col flex-shrink-0"
      style={{ background: "var(--surface)", borderRight: "1px solid var(--border)" }}
    >
      {/* Header */}
      <div className="px-4 pt-5 pb-4">
        <div className="flex items-center gap-3 mb-5 px-1">
          <div
            className="w-[34px] h-[34px] rounded-[10px] flex items-center justify-center text-white"
            style={{
              background: "linear-gradient(135deg, var(--wave), var(--reef))",
              boxShadow: "0 4px 14px rgba(14,165,233,.25)",
            }}
          >
            <BrandIcon />
          </div>
          <span
            className="text-[18px] tracking-[-0.3px]"
            style={{ fontFamily: "var(--font-display)", color: "var(--text-primary)" }}
          >
            FloodMind
          </span>
        </div>

        <button
          onClick={onNewSession}
          className="group flex items-center gap-2 w-full px-3 py-2 rounded-xl text-[12px] font-semibold transition-all duration-200 active:scale-[0.97]"
          style={{
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            color: "var(--text-secondary)",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--surface-3)";
            e.currentTarget.style.color = "var(--text-primary)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "var(--surface-2)";
            e.currentTarget.style.color = "var(--text-secondary)";
          }}
        >
          <Plus size={14} strokeWidth={2} />
          <span>新建预报任务</span>
        </button>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-3 pb-3 flex flex-col">
        <div
          className="px-2 py-2 text-[10px] font-bold tracking-[0.08em] uppercase"
          style={{ color: "var(--text-tertiary)" }}
        >
          最近会话
        </div>
        <div className="flex flex-col gap-1">
          {sessions.map((session) => {
            const active = session.session_id === activeSessionId;
            return (
              <div
                key={session.session_id}
                className="group flex items-center gap-1 rounded-lg transition-all duration-200"
                style={{
                  background: active ? "var(--surface-2)" : "transparent",
                  boxShadow: active ? "inset 0 0 0 1px var(--border-strong)" : "none",
                }}
                onMouseEnter={(e) => {
                  if (!active) e.currentTarget.style.background = "var(--surface-2)";
                }}
                onMouseLeave={(e) => {
                  if (!active) e.currentTarget.style.background = "transparent";
                }}
              >
                <button
                  onClick={() => onSelectSession(session.session_id)}
                  className="flex-1 flex items-center gap-2.5 px-3 py-2 text-left rounded-lg text-[12px] transition-colors truncate"
                  style={{ color: active ? "var(--text-primary)" : "var(--text-secondary)" }}
                >
                  <MessageSquare
                    size={13}
                    strokeWidth={1.8}
                    style={{ opacity: active ? 1 : 0.55 }}
                  />
                  <span className="truncate">
                    {session.title && !session.title.startsWith("session-")
                      ? session.title
                      : "新会话"}
                  </span>
                </button>
                <button
                  onClick={() => onDeleteSession(session.session_id)}
                  className="mr-1 opacity-0 group-hover:opacity-100 transition-all duration-200 p-1.5 rounded-md"
                  style={{ color: "var(--text-tertiary)" }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "rgba(244,63,94,0.08)";
                    e.currentTarget.style.color = "var(--alert)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "transparent";
                    e.currentTarget.style.color = "var(--text-tertiary)";
                  }}
                  title="删除"
                >
                  <Trash2 size={12} strokeWidth={1.8} />
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* Footer */}
      <div
        className="px-3 py-3 flex flex-col gap-1"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        <button
          onClick={onShowScheduledTasks}
          className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-[12px] transition-colors duration-200 text-left"
          style={{ color: "var(--text-secondary)" }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--surface-2)";
            e.currentTarget.style.color = "var(--text-primary)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
            e.currentTarget.style.color = "var(--text-secondary)";
          }}
        >
          <Clock size={13} strokeWidth={1.8} />
          <span>定时任务</span>
        </button>
      </div>
    </div>
  );
}
