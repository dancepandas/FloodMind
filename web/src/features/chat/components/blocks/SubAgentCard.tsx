import { ChevronRight } from "lucide-react";
import type { ActionDetail } from "@/types/app";
import { StepComplete } from "./block-primitives";

interface SubAgentCardProps {
  action: ActionDetail;
  isExpanded: boolean;
  onToggle: () => void;
}

/** 嵌套子代理卡片：运行中显示脉冲加载，可展开看任务描述。 */
export function SubAgentCard({ action, isExpanded, onToggle }: SubAgentCardProps) {
  const taskLabel = action.delegation?.label || "子代理";
  const taskDesc = action.delegation?.task || action.delegation?.skill_name || "";
  const isRunning = action.status === "running";

  return (
    <div
      className="rounded-md overflow-hidden transition-all duration-200"
      style={{
        background: "var(--surface-2)",
        border: `1px solid ${isRunning ? "var(--wave)" : "var(--border-strong)"}`,
      }}
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-2.5 py-[6px] text-left transition-all duration-200"
        onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-3)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
      >
        {isRunning ? (
          <span className="codex-subagent-loader">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
          </span>
        ) : action.status === "done" ? (
          <StepComplete type="action" />
        ) : (
          <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="var(--alert)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        )}
        <span className="text-[11px] font-semibold" style={{ color: "var(--wave)", fontFamily: "var(--font-mono)" }}>
          {taskLabel}
        </span>
        {taskDesc && (
          <span className="text-[10px] truncate" style={{ color: "var(--text-tertiary)", opacity: 0.75 }}>
            — {taskDesc}
          </span>
        )}
        <span className="ml-auto">
          <ChevronRight size={11} style={{ color: "var(--text-tertiary)", opacity: 0.5, transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.2s" }} />
        </span>
      </button>
      {isExpanded && (
        <div className="px-2.5 pb-2 pt-0.5 animate-sub-agent-expand" style={{ borderTop: "1px solid var(--border-strong)" }}>
          <div className="pl-3 text-[10px] leading-relaxed" style={{ color: "var(--text-tertiary)", borderLeft: "2px solid var(--wave)" }}>
            {taskDesc || "子Agent 执行中..."}
          </div>
        </div>
      )}
    </div>
  );
}
