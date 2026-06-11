import { useState } from "react";
import { ChevronDown, ChevronRight, ListTodo, Hash } from "lucide-react";
import type { TodoState } from "@/types/app";

const STATUS_ICON: Record<string, string> = {
  pending: "○",
  in_progress: "◐",
  completed: "●",
  cancelled: "✕",
};

const STATUS_COLOR: Record<string, string> = {
  pending: "var(--muted-foreground)",
  in_progress: "var(--ocean-500)",
  completed: "var(--teal-500)",
  cancelled: "var(--destructive)",
};

interface AgentSidePanelProps {
  todos: TodoState;
  sessionTokenUsage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

function formatTokens(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return String(n);
}

export function AgentSidePanel({ todos, sessionTokenUsage }: AgentSidePanelProps) {
  const [todosExpanded, setTodosExpanded] = useState(true);
  const [tokensExpanded, setTokensExpanded] = useState(true);
  const items = todos?.items || [];
  const completed = items.filter((t) => t.status === "completed").length;
  const total = items.length;
  const hasTokens = sessionTokenUsage.total_tokens > 0;

  return (
    <div
      className="h-full flex flex-col flex-shrink-0 overflow-y-auto"
      style={{
        width: "260px",
        background: "var(--glass-bg)",
        borderLeft: "1px solid hsl(var(--border))",
        backdropFilter: "blur(12px)",
      }}
    >
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2.5 shrink-0"
        style={{ borderBottom: "1px solid hsl(var(--border))" }}
      >
        <div
          className="w-5 h-5 rounded flex items-center justify-center"
          style={{
            background: "var(--ocean-50)",
            color: "var(--ocean-500)",
          }}
        >
          <ListTodo size={12} strokeWidth={2} />
        </div>
        <span
          className="text-[11px] font-semibold tracking-tight flex-1"
          style={{ color: "hsl(var(--foreground))" }}
        >
          会话概览
        </span>
        {total > 0 && (
          <span
            className="text-[10px] font-mono px-1.5 py-0.5 rounded"
            style={{
              background: "hsl(var(--muted))",
              color: "hsl(var(--muted-foreground))",
            }}
          >
            {completed}/{total}
          </span>
        )}
      </div>

      {/* Todo Section */}
      <div className="shrink-0" style={{ borderBottom: "1px solid hsl(var(--border))" }}>
        <button
          onClick={() => setTodosExpanded(!todosExpanded)}
          className="w-full flex items-center gap-2 px-3 py-2 transition-colors duration-150"
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "hsl(var(--muted))";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
          }}
        >
          <span
            className="flex-shrink-0"
            style={{ color: "hsl(var(--muted-foreground))", opacity: 0.4 }}
          >
            {todosExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </span>
          <span className="text-[11px] font-semibold" style={{ color: "hsl(var(--foreground))" }}>
            任务列表
          </span>
        </button>

        {todosExpanded && (
          <div className="px-3 pb-2">
            {total === 0 ? (
              <div className="py-4 text-center" style={{ opacity: 0.35 }}>
                <ListTodo size={20} className="mx-auto mb-1.5" style={{ color: "hsl(var(--muted-foreground))" }} />
                <p className="text-[10px]" style={{ color: "hsl(var(--muted-foreground))" }}>
                  暂无任务
                </p>
                <p className="text-[9px] mt-0.5" style={{ color: "hsl(var(--muted-foreground))" }}>
                  LLM 在执行多步骤任务时会自动创建
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-0.5">
                {items.map((todo) => (
                  <div
                    key={todo.id}
                    className="flex items-start gap-1.5 py-1 px-1.5 rounded transition-colors duration-150"
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = "hsl(var(--muted))";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = "transparent";
                    }}
                  >
                    <span
                      className="text-[10px] leading-[1.6] flex-shrink-0 mt-px"
                      style={{ color: STATUS_COLOR[todo.status] || "hsl(var(--muted-foreground))" }}
                    >
                      {STATUS_ICON[todo.status] || "○"}
                    </span>
                    <span
                      className="text-[10px] leading-[1.4] break-words flex-1"
                      style={{
                        color:
                          todo.status === "completed"
                            ? "var(--teal-600)"
                            : todo.status === "cancelled"
                              ? "hsl(var(--destructive))"
                              : "hsl(var(--foreground))",
                        opacity: todo.status === "completed" || todo.status === "cancelled" ? 0.6 : 1,
                        textDecoration: todo.status === "cancelled" ? "line-through" : "none",
                      }}
                    >
                      {todo.content}
                    </span>
                    {todo.priority === "high" &&
                      todo.status !== "completed" &&
                      todo.status !== "cancelled" && (
                        <span
                          className="text-[8px] font-semibold flex-shrink-0 px-1 rounded"
                          style={{
                            background: "#fef3c7",
                            color: "var(--amber-600)",
                          }}
                        >
                          高
                        </span>
                      )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Token Usage Section */}
      <div className="shrink-0">
        <button
          onClick={() => setTokensExpanded(!tokensExpanded)}
          className="w-full flex items-center gap-2 px-3 py-2 transition-colors duration-150"
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "hsl(var(--muted))";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
          }}
        >
          <span
            className="flex-shrink-0"
            style={{ color: "hsl(var(--muted-foreground))", opacity: 0.4 }}
          >
            {tokensExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </span>
          <span className="text-[11px] font-semibold" style={{ color: "hsl(var(--foreground))" }}>
            Token 用量
          </span>
          {hasTokens && (
            <span
              className="ml-auto text-[10px] font-mono"
              style={{ color: "hsl(var(--muted-foreground))" }}
            >
              {formatTokens(sessionTokenUsage.total_tokens)}
            </span>
          )}
        </button>

        {tokensExpanded && (
          <div className="px-3 pb-3">
            {!hasTokens ? (
              <div className="py-4 text-center" style={{ opacity: 0.35 }}>
                <Hash size={20} className="mx-auto mb-1.5" style={{ color: "hsl(var(--muted-foreground))" }} />
                <p className="text-[10px]" style={{ color: "hsl(var(--muted-foreground))" }}>
                  暂无统计
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <span className="text-[10px]" style={{ color: "hsl(var(--muted-foreground))" }}>
                    输入 (prompt)
                  </span>
                  <span className="text-[10px] font-mono" style={{ color: "hsl(var(--foreground))" }}>
                    {formatTokens(sessionTokenUsage.prompt_tokens)}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px]" style={{ color: "hsl(var(--muted-foreground))" }}>
                    输出 (completion)
                  </span>
                  <span className="text-[10px] font-mono" style={{ color: "hsl(var(--foreground))" }}>
                    {formatTokens(sessionTokenUsage.completion_tokens)}
                  </span>
                </div>
                <div
                  className="flex items-center justify-between pt-1.5"
                  style={{ borderTop: "1px solid hsl(var(--border))" }}
                >
                  <span className="text-[10px] font-semibold" style={{ color: "hsl(var(--foreground))" }}>
                    总计
                  </span>
                  <span
                    className="text-[10px] font-mono font-semibold"
                    style={{ color: "var(--ocean-500)" }}
                  >
                    {formatTokens(sessionTokenUsage.total_tokens)}
                  </span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
