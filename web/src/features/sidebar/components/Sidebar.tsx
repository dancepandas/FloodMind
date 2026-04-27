import { Bot, MessageSquare, Plus, Trash2 } from "lucide-react";
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
    <div className="w-[280px] h-full bg-sidebar border-r border-sidebar-border flex flex-col flex-shrink-0">
      <div className="px-5 pt-5 pb-4 flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center text-primary">
            <Bot size={20} />
          </div>
          <span className="font-semibold text-[17px] text-foreground tracking-tight relative">
            FloodMind
            <sup className="absolute -top-1.5 -right-7 text-[9px] font-semibold text-primary bg-primary/8 px-1.5 rounded-md leading-none py-0.5">beta</sup>
          </span>
        </div>

        <button onClick={onNewSession} className="flex items-center gap-2 w-full px-3.5 py-2.5 bg-background border border-border rounded-xl hover:bg-accent hover:border-accent-foreground/15 transition-all duration-200 text-sm font-medium active:scale-[0.98]">
          <Plus size={15} className="text-muted-foreground" />
          <span>新建会话</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-4 flex flex-col gap-0.5">
        <div className="px-3 py-2 text-[11px] font-semibold text-muted-foreground tracking-widest uppercase">
          最近会话
        </div>
        {sessions.map((session) => {
          const active = session.session_id === activeSessionId;
          return (
            <div key={session.session_id} className={`group flex items-center gap-1 rounded-xl transition-colors duration-150 ${active ? "bg-accent" : "hover:bg-accent/60"}`}>
              <button
                onClick={() => onSelectSession(session.session_id)}
                className={`flex-1 flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition-colors text-left truncate ${active ? "text-foreground font-medium" : "text-muted-foreground hover:text-foreground"}`}
              >
                <MessageSquare size={15} className={`flex-shrink-0 ${active ? "text-primary" : ""}`} />
                <span className="truncate">{session.title || "未命名会话"}</span>
              </button>
              <button
                onClick={() => onDeleteSession(session.session_id)}
                className="mr-2 opacity-0 group-hover:opacity-100 text-muted-foreground/60 hover:text-destructive transition-all duration-150"
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
