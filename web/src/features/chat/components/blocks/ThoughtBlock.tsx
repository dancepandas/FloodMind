import { Brain, ChevronRight } from "lucide-react";
import type { ChatMessage as ChatMessageModel, MessageBlock } from "@/types/app";
import { StepBadge, StepComplete, StreamingIndicator } from "./block-primitives";

interface ThoughtBlockProps {
  message: ChatMessageModel;
  block: MessageBlock;
  onToggleThought: (messageId: string, blockId: string) => void;
  stepIndex: number;
}

/** 可折叠的思考过程块（V4 风格）。流式时显示 "Thinking" + 脉冲；完成后折叠归档。 */
export function ThoughtBlock({ message, block, onToggleThought, stepIndex }: ThoughtBlockProps) {
  const isCollapsed = block.isCollapsed;
  const isStreaming = block.isStreaming;
  const isArchived = block.isArchived;

  return (
    <div className={`w-full transition-all duration-300 ${isArchived ? "opacity-40" : "opacity-100"}`}>
      {/* Header bar */}
      <button
        type="button"
        onClick={() => onToggleThought(message.id, block.id)}
        className="w-full flex items-center gap-2 px-2.5 py-[7px] rounded-lg text-left transition-all duration-200 group"
        style={{
          background: isCollapsed ? "transparent" : "var(--surface-2)",
          border: `1px solid ${isCollapsed ? "transparent" : "var(--border-strong)"}`,
        }}
        onMouseEnter={(e) => { if (isCollapsed) e.currentTarget.style.background = "var(--surface-2)"; }}
        onMouseLeave={(e) => { if (isCollapsed) e.currentTarget.style.background = "transparent"; }}
      >
        {isStreaming ? <StepBadge index={stepIndex} type="thought" /> : <StepComplete type="thought" />}
        <Brain size={12} style={{ color: "var(--wave)", opacity: isStreaming ? 1 : 0.5 }} />
        <span className="text-[11px] font-semibold" style={{ color: "var(--wave)" }}>
          {isStreaming ? "Thinking" : "Thought"}
        </span>
        {isStreaming && <StreamingIndicator />}
        <span className="ml-auto flex-shrink-0 transition-transform duration-200" style={{ transform: isCollapsed ? "rotate(0deg)" : "rotate(0deg)" }}>
          <ChevronRight size={12} style={{ color: "var(--text-tertiary)", opacity: 0.5, transform: isCollapsed ? "rotate(0deg)" : "rotate(90deg)", transition: "transform 0.2s" }} />
        </span>
      </button>

      {/* Collapsible content */}
      <div
        className={`overflow-hidden transition-all duration-300 ${isCollapsed ? "max-h-0 opacity-0" : "max-h-80 opacity-100 overflow-y-auto"}`}
      >
        <div
          className="ml-[26px] mr-1 mt-1 px-3 py-2 rounded-lg text-[11px] leading-relaxed"
          style={{
            background: "var(--surface-2)",
            borderLeft: "2px solid var(--wave)",
            color: "var(--text-secondary)",
          }}
        >
          <div className="whitespace-pre-wrap break-words max-w-[65ch]">{block.content}</div>
          {/* 流式指示由 header 的 StreamingIndicator 统一提供（降噪：不再叠加扫描线） */}
        </div>
      </div>
    </div>
  );
}
