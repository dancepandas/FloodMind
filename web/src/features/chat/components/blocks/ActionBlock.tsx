import { useState } from "react";
import { Terminal, ChevronRight } from "lucide-react";
import type { ActionDetail, ChatMessage as ChatMessageModel, MessageBlock } from "@/types/app";
import { getToolDisplayName } from "@/features/chat/lib/message-blocks";
import { StepBadge, StepComplete, StreamingIndicator, StatusIcon } from "./block-primitives";
import { SubAgentCard } from "./SubAgentCard";

interface ActionBlockProps {
  block: MessageBlock;
  message: ChatMessageModel;
  onToggleThought: (messageId: string, blockId: string) => void;
  stepIndex: number;
}

const SUB_AGENT_TOOLS = new Set(["SubAgent", "ParallelSubAgent", "ParallelTask"]);

/** 工具调用块：顶层工具 + 按 step_key 分组的子代理内部工具 + SubAgent 卡片。 */
export function ActionBlock({ block, onToggleThought, message, stepIndex }: ActionBlockProps) {
  const actions = block.actions || [];
  const isStreaming = block.isStreaming;
  const isCollapsed = block.isCollapsed;
  const isArchived = block.isArchived;
  const [expandedSubAgents, setExpandedSubAgents] = useState<Set<string>>(new Set());
  const [expandedStepGroups, setExpandedStepGroups] = useState<Set<string>>(new Set());

  const subAgentActions = actions.filter((a) => SUB_AGENT_TOOLS.has(a.toolName));
  const toolActions = actions.filter((a) => !SUB_AGENT_TOOLS.has(a.toolName));

  // Separate top-level tool actions from sub-agent internal tool actions by step_key
  const ungroupedToolActions = toolActions.filter((a) => !a.step_key);
  const toolActionsByStep: Record<string, ActionDetail[]> = {};
  for (const a of toolActions) {
    if (a.step_key) {
      if (!toolActionsByStep[a.step_key]) toolActionsByStep[a.step_key] = [];
      toolActionsByStep[a.step_key].push(a);
    }
  }
  const stepGroups = Object.entries(toolActionsByStep);

  const toggleSubAgent = (callId: string) => {
    setExpandedSubAgents((prev) => {
      const next = new Set(prev);
      if (next.has(callId)) next.delete(callId);
      else next.add(callId);
      return next;
    });
  };

  const toggleStepGroup = (stepKey: string) => {
    setExpandedStepGroups((prev) => {
      const next = new Set(prev);
      if (next.has(stepKey)) next.delete(stepKey);
      else next.add(stepKey);
      return next;
    });
  };

  const runningCount = actions.filter((a) => a.status === "running" || a.status === "pending_confirmation").length;
  const doneCount = actions.filter((a) => a.status === "done").length;

  return (
    <div className={`w-full transition-all duration-300 ${isArchived ? "opacity-40" : "opacity-100"}`}>
      {/* Header */}
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
        {isStreaming ? <StepBadge index={stepIndex} type="action" /> : <StepComplete type="action" />}
        <Terminal size={12} style={{ color: "var(--reef)", opacity: isStreaming ? 1 : 0.5 }} />
        <span className="text-[11px] font-semibold" style={{ color: "var(--reef)" }}>
          {isStreaming
            ? `Running${runningCount > 0 ? ` ${runningCount}项` : ""}`
            : `${doneCount > 0 ? `${doneCount}项完成` : `${actions.length}项操作`}`}
        </span>
        {isStreaming && <StreamingIndicator variant="teal" />}
        <span className="ml-auto">
          <ChevronRight size={12} style={{ color: "var(--text-tertiary)", opacity: 0.5, transform: isCollapsed ? "rotate(0deg)" : "rotate(90deg)", transition: "transform 0.2s" }} />
        </span>
      </button>

      {/* Collapsible content */}
      <div className={`overflow-hidden transition-all duration-300 ${isCollapsed ? "max-h-0 opacity-0" : "max-h-72 opacity-100"}`}>
        <div
          className="ml-[26px] mr-1 mt-1 px-3 py-2 rounded-lg text-[11px] leading-relaxed overflow-y-auto"
          style={{
            background: "var(--surface-2)",
            borderLeft: "2px solid var(--reef)",
          }}
        >
          {/* Top-level tool calls (no step_key) */}
          {ungroupedToolActions.length > 0 && (
            <div className="flex flex-col gap-[6px]">
              {ungroupedToolActions.map((action) => {
                const displayName = getToolDisplayName(action.toolName);
                return (
                  <div key={action.callId || action.toolName} className="flex items-center gap-2">
                    <StatusIcon status={action.status} size={10} />
                    <span
                      className="text-[11px] font-medium"
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: action.status === "running" ? "var(--text-primary)" : "var(--text-secondary)",
                        opacity: action.status === "done" ? 0.7 : 1,
                      }}
                    >
                      {displayName}
                    </span>
                    {action.content && action.status === "done" && (
                      <span className="text-[10px] truncate" style={{ color: "var(--text-tertiary)", maxWidth: "200px" }}>
                        {action.content.slice(0, 60)}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Sub-agent internal tool calls grouped by step_key */}
          {stepGroups.length > 0 && (() => {
            const allExpanded = expandedStepGroups.has("__all__");
            const totalDone = stepGroups.reduce((sum, [, acts]) => sum + acts.filter(a => a.status === "done").length, 0);
            const totalCount = stepGroups.reduce((sum, [, acts]) => sum + acts.length, 0);
            const stepLabels = stepGroups.map((_, i) => `子任务 ${i + 1}`).join("、");
            return (
            <div className={`${ungroupedToolActions.length > 0 ? "mt-2 pt-2" : ""}`} style={ungroupedToolActions.length > 0 ? { borderTop: "1px dashed var(--border-strong)" } : undefined}>
              <button
                type="button"
                onClick={() => toggleStepGroup("__all__")}
                className="w-full flex items-center gap-1.5 px-2 py-1 rounded-md text-left transition-colors duration-150 hover:opacity-80"
                style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
              >
                <ChevronRight size={9} style={{ color: "var(--wave)", transform: allExpanded ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.2s" }} />
                <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="var(--wave)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/>
                </svg>
                <span className="text-[10px] font-semibold" style={{ color: "var(--wave)" }}>
                  {stepGroups.length}个子代理
                </span>
                <span className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>
                  {totalDone}/{totalCount}项
                </span>
                <span className="text-[9px] truncate ml-auto" style={{ color: "var(--text-tertiary)", opacity: 0.6, maxWidth: "180px" }}>
                  {stepLabels}
                </span>
              </button>
              {allExpanded && (
                <div className="flex flex-col gap-2 mt-1.5 ml-2">
                  {stepGroups.map(([stepKey, stepActions], groupIndex) => (
                    <div key={stepKey} className="rounded-md overflow-hidden" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                      <div className="flex items-center gap-1.5 px-2 py-1" style={{ borderBottom: "1px solid var(--border)" }}>
                        <span className="text-[10px] font-semibold" style={{ color: "var(--wave)" }}>{`子任务 ${groupIndex + 1}`}</span>
                        <span className="text-[9px]" style={{ color: "var(--text-tertiary)" }}>
                          {stepActions.filter(a => a.status === "done").length}/{stepActions.length}
                        </span>
                      </div>
                      <div className="flex flex-col gap-[4px] px-2 py-1.5">
                        {stepActions.map((action) => {
                          const displayName = getToolDisplayName(action.toolName);
                          return (
                            <div key={action.callId || action.toolName} className="flex items-center gap-2">
                              <StatusIcon status={action.status} size={10} />
                              <span className="text-[11px] font-medium" style={{
                                fontFamily: "var(--font-mono)",
                                color: action.status === "running" ? "var(--text-primary)" : "var(--text-secondary)",
                                opacity: action.status === "done" ? 0.7 : 1,
                              }}>
                                {displayName}
                              </span>
                              {action.content && action.status === "done" && (
                                <span className="text-[10px] truncate" style={{ color: "var(--text-tertiary)", maxWidth: "150px" }}>
                                  {action.content.slice(0, 40)}
                                </span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            );
          })()}

          {/* SubAgents */}
          {subAgentActions.length > 0 && (
            <div className={`flex flex-col gap-1.5 ${(ungroupedToolActions.length > 0 || stepGroups.length > 0) ? "mt-2 pt-2" : ""}`} style={(ungroupedToolActions.length > 0 || stepGroups.length > 0) ? { borderTop: "1px dashed var(--border-strong)" } : undefined}>
              {subAgentActions.map((action) => (
                <SubAgentCard
                  key={action.callId}
                  action={action}
                  isExpanded={expandedSubAgents.has(action.callId)}
                  onToggle={() => toggleSubAgent(action.callId)}
                />
              ))}
            </div>
          )}

          {/* 流式指示由 header 的 StreamingIndicator 统一提供（降噪：不再叠加扫描线） */}
        </div>
      </div>
    </div>
  );
}
