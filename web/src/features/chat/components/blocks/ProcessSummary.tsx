import { ChevronRight } from "lucide-react";
import { StreamingIndicator } from "./block-primitives";

interface ProcessSummaryProps {
  /** 过程总步数（thought + action） */
  stepCount: number;
  thoughtCount: number;
  actionCount: number;
  /** 是否处于流式运行中（决定脉冲指示 vs 静态完成指示） */
  isStreaming: boolean;
  isExpanded: boolean;
  onToggle: () => void;
}

/**
 * 过程折叠摘要行 —— 流式态与完成态共用的统一入口（CC 风）。
 *
 * 设计原则：
 * - 过程低权重：一行摘要，字号 / 对比度均低于回答块，作为"可回看的上下文"而非主内容；
 * - 单一进度指示：运行中仅一个脉冲点（StreamingIndicator），不与扫描线 / 跳动叠加；
 * - 回答为主：过程折叠后，回答块成为视觉焦点（由 ChatMessage 保证）。
 */
export function ProcessSummary({
  stepCount,
  thoughtCount,
  actionCount,
  isStreaming,
  isExpanded,
  onToggle,
}: ProcessSummaryProps) {
  // 无过程且非运行中时不渲染（完成态无中间步骤的消息不显示入口）
  if (stepCount === 0 && !isStreaming) return null;

  const detail = [
    thoughtCount > 0 && `${thoughtCount} 思考`,
    actionCount > 0 && `${actionCount} 工具`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <button
      type="button"
      onClick={onToggle}
      className="flex items-center gap-1.5 self-start rounded-md px-2 py-1 text-[10px] font-medium transition-all duration-200"
      style={{
        background: isExpanded ? "var(--surface-2)" : "transparent",
        border: `1px solid ${isExpanded ? "var(--border-strong)" : "var(--border)"}`,
        color: isExpanded ? "var(--text-secondary)" : "var(--text-tertiary)",
      }}
      onMouseEnter={(e) => {
        if (!isExpanded) {
          e.currentTarget.style.background = "var(--surface-2)";
          e.currentTarget.style.borderColor = "var(--border-strong)";
        }
      }}
      onMouseLeave={(e) => {
        if (!isExpanded) {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.borderColor = "var(--border)";
        }
      }}
      aria-expanded={isExpanded}
    >
      {isStreaming ? (
        <StreamingIndicator variant="ocean" />
      ) : (
        <span
          className="inline-flex items-center justify-center w-[10px] h-[10px] rounded-full flex-shrink-0"
          style={{ background: "var(--surface-3)" }}
          aria-hidden
        >
          <span style={{ width: 4, height: 4, borderRadius: "50%", background: "var(--text-tertiary)" }} />
        </span>
      )}
      <span>{isStreaming ? `处理中${stepCount > 0 ? ` · ${stepCount} 步` : ""}` : `${stepCount} 步过程`}</span>
      {detail && (
        <span style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>{detail}</span>
      )}
      <ChevronRight
        size={9}
        style={{
          color: "var(--text-tertiary)",
          opacity: 0.5,
          transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)",
          transition: "transform 0.2s",
        }}
      />
    </button>
  );
}
