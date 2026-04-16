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
    <div className="w-[280px] h-full bg-[rgba(238,246,255,0.88)] border-r border-border flex flex-col flex-shrink-0 backdrop-blur-sm">
      <div className="p-4 flex flex-col gap-4">
        <div className="flex items-center gap-2 px-2">
          <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center text-primary-foreground">
            <Bot size={20} />
          </div>
          <span className="font-semibold text-lg text-foreground">洪水智能体</span>
        </div>

        <button onClick={onNewSession} className="flex items-center gap-2 w-full px-3 py-2 bg-background border border-border rounded-md hover:bg-muted transition-colors text-sm font-medium">
          <Plus size={16} />
          新建会话
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-4 flex flex-col gap-1">
        <div className="px-3 py-2 text-xs font-semibold text-muted-foreground tracking-wider">
          最近会话
        </div>
        {sessions.map((session) => {
          const active = session.session_id === activeSessionId;
          return (
            <div key={session.session_id} className={`group flex items-center gap-2 px-2 py-1 rounded-md ${active ? "bg-muted" : "hover:bg-muted/50"}`}>
              <button
                onClick={() => onSelectSession(session.session_id)}
                className={`flex-1 flex items-center gap-3 px-2 py-2 rounded-md text-sm transition-colors text-left truncate ${active ? "text-foreground font-medium" : "text-muted-foreground hover:text-foreground"}`}
              >
                <MessageSquare size={16} className="flex-shrink-0" />
                <span className="truncate">{session.title || "未命名会话"}</span>
              </button>
              <button
                onClick={() => onDeleteSession(session.session_id)}
                className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-opacity"
                title="删除会话"
              >
                <Trash2 size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
