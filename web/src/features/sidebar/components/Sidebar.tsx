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
      style={{ background: 'var(--sidebar-bg)', borderRight: '1px solid var(--sidebar-border)' }}
    >
      {/* Header */}
      <div className="px-4 pt-5 pb-4">
        <div className="flex items-center gap-3 mb-5">
          <div className="relative">
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center animate-glow-pulse"
              style={{
                background: 'var(--ocean-500)',
                boxShadow: '0 4px 16px rgba(37, 99, 168, 0.3)',
              }}
            >
              <img src="/floodmind-icon.svg" alt="FloodMind" className="w-5.5 h-5.5" style={{ filter: "brightness(0) invert(1)" }} />
            </div>
            <div className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2" style={{ background: 'var(--teal-400)', borderColor: 'var(--sidebar-bg)' }} />
          </div>
          <div className="flex flex-col">
            <span
              className="font-semibold text-[16px] tracking-tight leading-none"
              style={{ fontFamily: 'var(--font-display)', color: 'hsl(var(--foreground))' }}
            >
              FloodMind
            </span>
            <span
              className="text-[9px] font-semibold tracking-[0.18em] mt-1.5 uppercase"
              style={{ color: 'var(--ocean-400)' }}
            >
              Hydro Forecast Agent
            </span>
          </div>
        </div>

        <button
          onClick={onNewSession}
          className="group relative overflow-hidden flex items-center justify-center gap-2 w-full px-3.5 py-2.5 rounded-xl text-[13px] font-medium transition-all duration-300 active:scale-[0.97]"
          style={{
            background: 'var(--gradient-subtle)',
            border: '1px solid var(--ocean-200)',
            color: 'var(--ocean-700)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'var(--gradient-ocean-teal)';
            e.currentTarget.style.color = 'white';
            e.currentTarget.style.borderColor = 'transparent';
            e.currentTarget.style.boxShadow = '0 4px 20px rgba(37, 99, 168, 0.28)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'var(--gradient-subtle)';
            e.currentTarget.style.color = 'var(--ocean-700)';
            e.currentTarget.style.borderColor = 'var(--ocean-200)';
            e.currentTarget.style.boxShadow = 'none';
          }}
        >
          <Plus size={14} className="transition-transform duration-300 group-hover:rotate-90" strokeWidth={2.2} />
          <span>新建预报任务</span>
        </button>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-3 pb-3 flex flex-col gap-0.5">
        <div
          className="px-2.5 py-2 text-[9px] font-bold tracking-[0.16em] uppercase"
          style={{ color: 'var(--ocean-400)', opacity: 0.4 }}
        >
          任务记录
        </div>
        {sessions.map((session, idx) => {
          const active = session.session_id === activeSessionId;
          return (
            <div
              key={session.session_id}
              className={`group relative flex items-center gap-0.5 rounded-lg transition-all duration-250 animate-slide-in-right`}
              style={{
                animationDelay: `${idx * 40}ms`,
                background: active ? 'var(--sidebar-active)' : 'transparent',
              }}
              onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'var(--sidebar-hover)'; }}
              onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent'; }}
            >
              {active && (
                <div
                  className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full"
                  style={{ background: 'var(--gradient-ocean)' }}
                />
              )}
              <button
                onClick={() => onSelectSession(session.session_id)}
                className={`flex-1 flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-[12px] transition-colors text-left truncate ${
                  active ? 'font-medium pl-4' : ''
                }`}
                style={{ color: active ? 'hsl(var(--foreground))' : 'hsl(var(--muted-foreground))' }}
              >
                <div className="flex-shrink-0 relative">
                  <MessageSquare
                    size={13}
                    strokeWidth={1.8}
                    style={{ color: active ? 'var(--ocean-500)' : 'hsl(var(--muted-foreground))', opacity: active ? 1 : 0.35 }}
                  />
                  {active && (
                    <div className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full animate-pulse-subtle" style={{ background: 'var(--ocean-400)' }} />
                  )}
                </div>
                <span className="truncate">{(session.title && !session.title.startsWith('session-')) ? session.title : "新会话"}</span>
              </button>
              <button
                onClick={() => onDeleteSession(session.session_id)}
                className="mr-1 opacity-0 group-hover:opacity-100 transition-all duration-200 p-1 rounded-lg"
                style={{ color: 'hsl(var(--muted-foreground))' }}
                onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(200,50,35,0.08)'; e.currentTarget.style.color = 'hsl(var(--destructive))'; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'hsl(var(--muted-foreground))'; }}
                title="删除"
              >
                <Trash2 size={12} strokeWidth={1.8} />
              </button>
            </div>
          );
        })}
      </div>

      {/* Status Bar */}
      <div
        className="px-4 py-3 flex items-center gap-4"
        style={{ borderTop: '1px solid var(--sidebar-border)' }}
      >
        {[
          { Icon: Wifi, label: 'API', color: 'var(--teal-500)' },
          { Icon: Database, label: 'RAG', color: 'var(--teal-400)' },
          { Icon: Cpu, label: 'LLM', color: 'var(--ocean-400)' },
        ].map(({ Icon, label, color }) => (
          <div key={label} className="flex items-center gap-1.5">
            <div className="relative">
              <Icon size={9} style={{ color }} strokeWidth={2.5} />
              <div className="absolute inset-0 animate-ripple rounded-full" style={{ background: color, animationDuration: '3s' }} />
            </div>
            <span className="text-[9px] font-semibold tracking-wider" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.45 }}>
              {label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}