import { MessageSquare, Plus, Trash2, Wifi, Database, Cpu } from "lucide-react";
import type { SessionSummary } from "@/types/app";

interface SidebarProps {
  sessions: SessionSummary[];
  activeSessionId: string;
  onNewSession: () => void;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
}

export function Sidebar({
  sessions,
  activeSessionId,
  onNewSession,
  onSelectSession,
  onDeleteSession,
}: SidebarProps) {
  return (
    <div
      className="w-[260px] h-full flex flex-col flex-shrink-0"
      style={{ background: 'var(--sidebar-bg)', borderRight: '1px solid hsl(var(--border))' }}
    >
      {/* Header */}
      <div className="px-4 pt-5 pb-4">
        <div className="flex items-center gap-3 mb-4">
          <div
            className="w-9 h-9 rounded-xl flex items-center justify-center"
            style={{
              background: 'var(--ocean-500)',
              boxShadow: '0 4px 12px rgba(37, 99, 168, 0.25)',
            }}
          >
            <img src="/floodmind-icon.svg" alt="FloodMind" className="w-5 h-5" style={{ filter: "brightness(0) invert(1)" }} />
          </div>
          <div className="flex flex-col">
            <span
              className="font-semibold text-[15px] tracking-tight leading-none"
              style={{ fontFamily: 'var(--font-display)', color: 'hsl(var(--foreground))' }}
            >
              FloodMind
            </span>
            <span
              className="text-[9px] font-semibold tracking-[0.16em] mt-1 uppercase"
              style={{ color: 'var(--ocean-500)' }}
            >
              Agent Console
            </span>
          </div>
        </div>

        <button
          onClick={onNewSession}
          className="group flex items-center justify-center gap-2 w-full px-3.5 py-2.5 rounded-xl text-[13px] font-medium transition-all duration-200 active:scale-[0.97]"
          style={{
            background: 'linear-gradient(135deg, var(--ocean-50), var(--teal-50))',
            border: '1px solid var(--ocean-200)',
            color: 'var(--ocean-700)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'linear-gradient(135deg, var(--ocean-500), var(--teal-600))';
            e.currentTarget.style.color = 'white';
            e.currentTarget.style.borderColor = 'transparent';
            e.currentTarget.style.boxShadow = '0 4px 16px rgba(37, 99, 168, 0.25)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'linear-gradient(135deg, var(--ocean-50), var(--teal-50))';
            e.currentTarget.style.color = 'var(--ocean-700)';
            e.currentTarget.style.borderColor = 'var(--ocean-200)';
            e.currentTarget.style.boxShadow = 'none';
          }}
        >
          <Plus size={14} className="transition-transform duration-200 group-hover:rotate-90" />
          <span>新建预报任务</span>
        </button>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-3 pb-3 flex flex-col gap-0.5">
        <div
          className="px-2.5 py-2 text-[9px] font-bold tracking-[0.14em] uppercase"
          style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.5 }}
        >
          任务记录
        </div>
        {sessions.map((session) => {
          const active = session.session_id === activeSessionId;
          return (
            <div
              key={session.session_id}
              className={`group relative flex items-center gap-0.5 rounded-lg transition-all duration-200 ${
                active ? '' : 'hover:bg-[var(--sidebar-hover)]'
              }`}
              style={active ? { background: 'var(--sidebar-active)' } : {}}
            >
              {active && (
                <div
                  className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full"
                  style={{ background: 'var(--ocean-500)' }}
                />
              )}
              <button
                onClick={() => onSelectSession(session.session_id)}
                className={`flex-1 flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-[12px] transition-colors text-left truncate ${
                  active ? 'font-medium pl-4' : ''
                }`}
                style={{ color: active ? 'hsl(var(--foreground))' : 'hsl(var(--muted-foreground))' }}
              >
                <MessageSquare
                  size={13}
                  className="flex-shrink-0"
                  strokeWidth={1.8}
                  style={{ color: active ? 'var(--ocean-500)' : 'hsl(var(--muted-foreground))', opacity: active ? 1 : 0.4 }}
                />
                <span className="truncate">{session.title || "未命名任务"}</span>
              </button>
              <button
                onClick={() => onDeleteSession(session.session_id)}
                className="mr-1.5 opacity-0 group-hover:opacity-100 transition-all duration-200 p-0.5 rounded hover:bg-red-50"
                style={{ color: 'hsl(var(--muted-foreground))' }}
                title="删除"
              >
                <Trash2 size={12} />
              </button>
            </div>
          );
        })}
      </div>

      {/* Status Bar */}
      <div
        className="px-4 py-3 flex items-center gap-4"
        style={{ borderTop: '1px solid hsl(var(--border))' }}
      >
        {[
          { Icon: Wifi, label: 'API' },
          { Icon: Database, label: 'RAG' },
          { Icon: Cpu, label: 'LLM' },
        ].map(({ Icon, label }) => (
          <div key={label} className="flex items-center gap-1.5">
            <Icon size={10} style={{ color: 'var(--teal-500)' }} strokeWidth={2} />
            <span className="text-[9px] font-medium" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.5 }}>
              {label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
