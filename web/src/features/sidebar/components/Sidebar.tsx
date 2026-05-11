import { MessageSquare, Plus, Trash2 } from "lucide-react";
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
    <div className="w-[280px] h-full bg-sidebar border-r border-sidebar-border/60 flex flex-col flex-shrink-0">
      <div className="px-5 pt-5 pb-4 flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-primary/15 to-primary/5 flex items-center justify-center shadow-[0_4px_12px_-2px_rgba(59,107,208,0.12)]">
            <img src="/floodmind-icon.svg" alt="FloodMind" className="w-5 h-5 text-primary" />
          </div>
          <span className="font-bold text-[17px] text-foreground tracking-tight relative">
            FloodMind
            <sup className="absolute -top-2 -right-8 text-[8px] font-bold text-primary bg-primary/8 px-1.5 py-0.5 rounded-md leading-none tracking-wider">BETA</sup>
          </span>
        </div>

        <button
          onClick={onNewSession}
          className="flex items-center justify-center gap-2 w-full px-3.5 py-2.5 bg-primary/[0.04] border border-border/60 rounded-xl hover:bg-primary hover:text-primary-foreground hover:border-primary hover:shadow-[0_4px_12px_-2px_rgba(59,107,208,0.18)] transition-all duration-250 text-sm font-medium active:scale-[0.98] group"
        >
          <Plus size={15} className="text-muted-foreground group-hover:text-primary-foreground transition-colors duration-200" />
          <span>新建会话</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-4 flex flex-col gap-0.5">
        <div className="px-3 py-2 text-[10px] font-bold text-muted-foreground/45 tracking-[0.12em] uppercase">
          最近会话
        </div>
        {sessions.map((session) => {
          const active = session.session_id === activeSessionId;
          return (
            <div key={session.session_id} className={`group flex items-center gap-1 rounded-xl transition-all duration-200 ${active ? "bg-primary/[0.06]" : "hover:bg-accent/60"}`}>
              <button
                onClick={() => onSelectSession(session.session_id)}
                className={`flex-1 flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition-colors text-left truncate ${active ? "text-foreground font-medium" : "text-muted-foreground hover:text-foreground"}`}
              >
                <MessageSquare size={14} className={`flex-shrink-0 ${active ? "text-primary" : "text-muted-foreground/50"}`} strokeWidth={1.8} />
                <span className="truncate">{session.title || "未命名会话"}</span>
              </button>
              <button
                onClick={() => onDeleteSession(session.session_id)}
                className="mr-2 opacity-0 group-hover:opacity-100 text-muted-foreground/40 hover:text-destructive transition-all duration-200 p-0.5 rounded-md hover:bg-destructive/5"
                title="删除会话"
              >
                <Trash2 size={13} />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
