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
    <div className="w-[260px] h-full bg-sidebar border-r border-sidebar-border/50 flex flex-col flex-shrink-0">
      <div className="px-4 pt-4 pb-3 flex flex-col gap-3">
        <div className="flex items-center gap-2.5 px-1">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary/12 to-primary/3 flex items-center justify-center border border-primary/8">
            <img src="/floodmind-icon.svg" alt="FloodMind" className="w-4.5 h-4.5" />
          </div>
          <div className="flex flex-col">
            <span className="font-bold text-[14px] text-foreground tracking-tight leading-none">
              FloodMind
            </span>
            <span className="text-[9px] text-primary/70 font-semibold tracking-wider mt-0.5">AGENT CONSOLE</span>
          </div>
        </div>

        <button
          onClick={onNewSession}
          className="flex items-center justify-center gap-2 w-full px-3 py-2 bg-primary/[0.05] border border-border/50 rounded-lg hover:bg-primary hover:text-primary-foreground hover:border-primary hover:shadow-[0_4px_16px_-4px_rgba(38,92,178,0.15)] transition-all duration-250 text-[13px] font-medium active:scale-[0.98] group"
        >
          <Plus size={14} className="text-muted-foreground group-hover:text-primary-foreground transition-colors duration-200" />
          <span>新建预报任务</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2.5 pb-3 flex flex-col gap-0.5">
        <div className="px-2.5 py-1.5 text-[9px] font-bold text-muted-foreground/40 tracking-[0.14em] uppercase">
          任务记录
        </div>
        {sessions.map((session) => {
          const active = session.session_id === activeSessionId;
          return (
            <div key={session.session_id} className={`group relative flex items-center gap-0.5 rounded-lg transition-all duration-200 ${active ? "bg-primary/[0.06]" : "hover:bg-accent/50"}`}>
              {active && (
                <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-primary" />
              )}
              <button
                onClick={() => onSelectSession(session.session_id)}
                className={`flex-1 flex items-center gap-2.5 px-3 py-2 rounded-lg text-[12px] transition-colors text-left truncate ${active ? "text-foreground font-medium pl-4" : "text-muted-foreground hover:text-foreground"}`}
              >
                <MessageSquare size={13} className={`flex-shrink-0 ${active ? "text-primary" : "text-muted-foreground/40"}`} strokeWidth={1.8} />
                <span className="truncate">{session.title || "未命名任务"}</span>
              </button>
              <button
                onClick={() => onDeleteSession(session.session_id)}
                className="mr-1.5 opacity-0 group-hover:opacity-100 text-muted-foreground/30 hover:text-destructive transition-all duration-200 p-0.5 rounded hover:bg-destructive/5"
                title="删除"
              >
                <Trash2 size={12} />
              </button>
            </div>
          );
        })}
      </div>

      <div className="px-4 py-3 border-t border-border/30 flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          <Wifi size={10} className="text-emerald-500" strokeWidth={2} />
          <span className="text-[9px] text-muted-foreground/50 font-medium">API</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Database size={10} className="text-emerald-500" strokeWidth={2} />
          <span className="text-[9px] text-muted-foreground/50 font-medium">RAG</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Cpu size={10} className="text-emerald-500" strokeWidth={2} />
          <span className="text-[9px] text-muted-foreground/50 font-medium">LLM</span>
        </div>
      </div>
    </div>
  );
}
