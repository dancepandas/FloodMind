import { useState } from "react";
import { ChevronDown, ChevronRight, User, FileText, AlertTriangle, Eye, Download, ZoomIn, ExternalLink, Layers, X, AlertCircle, Terminal, Brain, MessageSquare } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage as ChatMessageModel, GeneratedArtifact, ReferenceLink, ActionDetail, MessageBlock } from "@/types/app";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { DocumentPreviewDialog, isPreviewable, getFileExt, getFileIcon } from "./DocumentPreviewDialog";
import { getToolDisplayName } from "@/features/chat/lib/message-blocks";
import { parseTaskList } from "@/features/chat/lib/parse-task-list";
import { CheckboxGroupList } from "@/features/chat/components/CheckboxGroupList";

interface ChatMessageProps {
  message: ChatMessageModel;
  onToggleThought: (messageId: string, blockId: string) => void;
  onUpdateAction?: (callId: string, status: ActionDetail["status"], content: string) => void;
  onQuickSubmit?: (text: string) => void;
}

const MAX_ARCHIVED_BLOCKS = 4;

/* ─── Codex-style Step Badge with tooltip ─── */
function StepBadge({ index, type, label }: { index: number; type: "thought" | "action" | "answer"; label?: string }) {
  const palette = {
    thought: { bg: 'var(--ocean-500)', ring: 'var(--ocean-200)' },
    action: { bg: 'var(--teal-500)', ring: 'var(--teal-200)' },
    answer: { bg: 'var(--ocean-400)', ring: 'var(--ocean-100)' },
  }[type];
  const tooltipText = label || { thought: '思考推理', action: '工具调用', answer: '应答' }[type];
  return (
    <span className="codex-step-tooltip">
      <span
        className="inline-flex items-center justify-center w-[18px] h-[18px] rounded-md text-[9px] font-bold flex-shrink-0"
        style={{ background: palette.bg, color: '#fff', boxShadow: `0 0 0 2px ${palette.ring}` }}
      >
        {index}
      </span>
      <span className="tooltip-content">{tooltipText}</span>
    </span>
  );
}

/* ─── Step Complete Checkbox ─── */
function StepComplete({ type }: { type: "thought" | "action" | "answer" }) {
  const borderColor = { thought: 'var(--ocean-300)', action: 'var(--teal-300)', answer: 'var(--ocean-200)' }[type];
  return (
    <span className={`codex-step-check completed`} style={{ borderColor }}>
      <span className="checkmark" />
    </span>
  );
}

/* ─── Streaming: Pulse Dots Loader ─── */
function StreamingIndicator({ variant = "ocean" }: { variant?: "ocean" | "teal" }) {
  return (
    <span className={`codex-pulse-dots ${variant === "teal" ? "teal" : ""}`}>
      <span className="dot" />
      <span className="dot" />
      <span className="dot" />
    </span>
  );
}

/* ─── Status Icon ─── */
function StatusIcon({ status, size = 12 }: { status: ActionDetail["status"]; size?: number }) {
  if (status === "running" || status === "pending_confirmation") {
    return <StreamingIndicator variant="teal" />;
  }
  if (status === "done") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="var(--teal-500)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="20 6 9 17 4 12" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="hsl(var(--destructive))" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

/* ═══════════════════════════════════════
   ThoughtBlock — Codex-style collapsible
   ═══════════════════════════════════════ */
function ThoughtBlock({ message, block, onToggleThought, stepIndex }: {
  message: ChatMessageModel; block: ChatMessageModel["blocks"][number];
  onToggleThought: (messageId: string, blockId: string) => void; stepIndex: number;
}) {
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
          background: isCollapsed ? 'transparent' : 'var(--thought-bg)',
          border: `1px solid ${isCollapsed ? 'transparent' : 'var(--thought-border)'}`,
        }}
        onMouseEnter={(e) => { if (isCollapsed) e.currentTarget.style.background = 'var(--thought-bg)'; }}
        onMouseLeave={(e) => { if (isCollapsed) e.currentTarget.style.background = 'transparent'; }}
      >
        {isStreaming ? <StepBadge index={stepIndex} type="thought" /> : <StepComplete type="thought" />}
        <Brain size={12} style={{ color: 'var(--ocean-400)', opacity: isStreaming ? 1 : 0.5 }} className={isStreaming ? 'animate-pulse-subtle' : ''} />
        <span className="text-[11px] font-semibold" style={{ color: 'var(--ocean-500)' }}>
          {isStreaming ? "Thinking" : "Thought"}
        </span>
        {isStreaming && <StreamingIndicator />}
        <span className="ml-auto flex-shrink-0 transition-transform duration-200" style={{ transform: isCollapsed ? 'rotate(0deg)' : 'rotate(0deg)' }}>
          <ChevronRight size={12} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35, transform: isCollapsed ? 'rotate(0deg)' : 'rotate(90deg)', transition: 'transform 0.2s' }} />
        </span>
      </button>

      {/* Collapsible content */}
      <div
        className={`overflow-hidden transition-all duration-300 ${isCollapsed ? "max-h-0 opacity-0" : "max-h-80 opacity-100 overflow-y-auto"}`}
      >
        <div
          className="ml-[26px] mr-1 mt-1 px-3 py-2 rounded-lg text-[11px] leading-relaxed"
          style={{
            background: 'var(--thought-bg)',
            borderLeft: '2px solid var(--ocean-300)',
            color: 'hsl(var(--muted-foreground))',
          }}
        >
          <div className="whitespace-pre-wrap break-words max-w-[65ch]">{block.content}</div>
          {isStreaming && (
            <div className="mt-1.5 h-[2px] w-12 rounded-full overflow-hidden" style={{ background: 'var(--ocean-100)' }}>
              <div className="h-full w-1/2 animate-shimmer-line" style={{ background: 'linear-gradient(to right, transparent, var(--ocean-400), transparent)' }} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════
   ActionBlock — Codex-style tool calls
   ═══════════════════════════════════════ */
function ActionBlock({ block, onToggleThought, message, onUpdateAction, stepIndex }: {
  block: ChatMessageModel["blocks"][number]; onToggleThought: (messageId: string, blockId: string) => void;
  message: ChatMessageModel; onUpdateAction?: (callId: string, status: ActionDetail["status"], content: string) => void;
  stepIndex: number;
}) {
  const actions = block.actions || [];
  const isStreaming = block.isStreaming;
  const isCollapsed = block.isCollapsed;
  const isArchived = block.isArchived;
  const [expandedSubAgents, setExpandedSubAgents] = useState<Set<string>>(new Set());
  const [expandedStepGroups, setExpandedStepGroups] = useState<Set<string>>(new Set());

  const subAgentActions = actions.filter((a) => a.toolName === 'SubAgent' || a.toolName === 'ParallelSubAgent' || a.toolName === 'ParallelTask');
  const toolActions = actions.filter((a) => a.toolName !== 'SubAgent' && a.toolName !== 'ParallelSubAgent' && a.toolName !== 'ParallelTask');

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
          background: isCollapsed ? 'transparent' : 'var(--action-bg)',
          border: `1px solid ${isCollapsed ? 'transparent' : 'var(--action-border)'}`,
        }}
        onMouseEnter={(e) => { if (isCollapsed) e.currentTarget.style.background = 'var(--action-bg)'; }}
        onMouseLeave={(e) => { if (isCollapsed) e.currentTarget.style.background = 'transparent'; }}
      >
        {isStreaming ? <StepBadge index={stepIndex} type="action" /> : <StepComplete type="action" />}
        <Terminal size={12} style={{ color: 'var(--teal-500)', opacity: isStreaming ? 1 : 0.5 }} className={isStreaming ? 'animate-pulse-subtle' : ''} />
        <span className="text-[11px] font-semibold" style={{ color: 'var(--teal-600)' }}>
          {isStreaming
            ? `Running${runningCount > 0 ? ` ${runningCount}项` : ''}`
            : `${doneCount > 0 ? `${doneCount}项完成` : `${actions.length}项操作`}`}
        </span>
        {isStreaming && <StreamingIndicator variant="teal" />}
        <span className="ml-auto">
          <ChevronRight size={12} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35, transform: isCollapsed ? 'rotate(0deg)' : 'rotate(90deg)', transition: 'transform 0.2s' }} />
        </span>
      </button>

      {/* Collapsible content */}
      <div className={`overflow-hidden transition-all duration-300 ${isCollapsed ? "max-h-0 opacity-0" : "max-h-72 opacity-100"}`}>
        <div
          className="ml-[26px] mr-1 mt-1 px-3 py-2 rounded-lg text-[11px] leading-relaxed overflow-y-auto"
          style={{
            background: 'var(--action-bg)',
            borderLeft: '2px solid var(--teal-300)',
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
                        fontFamily: 'var(--font-mono)',
                        color: action.status === "running" ? 'hsl(var(--foreground))' : 'hsl(var(--muted-foreground))',
                        opacity: action.status === "done" ? 0.7 : 1,
                      }}
                    >
                      {displayName}
                    </span>
                    {action.content && action.status === "done" && (
                      <span className="text-[10px] truncate" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.4, maxWidth: '200px' }}>
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
            const totalDone = stepGroups.reduce((sum, [, actions]) => sum + actions.filter(a => a.status === "done").length, 0);
            const totalCount = stepGroups.reduce((sum, [, actions]) => sum + actions.length, 0);
            const stepLabels = stepGroups.map(([key]) => key).join(", ");
            return (
            <div className={`${ungroupedToolActions.length > 0 ? 'mt-2 pt-2' : ''}`} style={ungroupedToolActions.length > 0 ? { borderTop: '1px dashed var(--action-border)' } : undefined}>
              <button
                type="button"
                onClick={() => toggleStepGroup("__all__")}
                className="w-full flex items-center gap-1.5 px-2 py-1 rounded-md text-left transition-colors duration-150 hover:opacity-80"
                style={{ background: 'var(--ocean-50)', border: '1px solid var(--ocean-100)' }}
              >
                <ChevronRight size={9} style={{ color: 'var(--ocean-400)', transform: allExpanded ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }} />
                <svg width={10} height={10} viewBox="0 0 24 24" fill="none" stroke="var(--ocean-400)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/>
                </svg>
                <span className="text-[10px] font-semibold" style={{ color: 'var(--ocean-500)' }}>
                  {stepGroups.length}个子代理
                </span>
                <span className="text-[9px]" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.4 }}>
                  {totalDone}/{totalCount}项
                </span>
                <span className="text-[9px] truncate ml-auto" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.25, maxWidth: '180px' }}>
                  {stepLabels}
                </span>
              </button>
              {allExpanded && (
                <div className="flex flex-col gap-2 mt-1.5 ml-2">
                  {stepGroups.map(([stepKey, stepActions]) => (
                    <div key={stepKey} className="rounded-md overflow-hidden" style={{ background: 'var(--ocean-50)', border: '1px solid var(--ocean-100)' }}>
                      <div className="flex items-center gap-1.5 px-2 py-1" style={{ borderBottom: '1px solid var(--ocean-100)' }}>
                        <span className="text-[10px] font-semibold" style={{ color: 'var(--ocean-500)' }}>{stepKey}</span>
                        <span className="text-[9px]" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.4 }}>
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
                                fontFamily: 'var(--font-mono)',
                                color: action.status === "running" ? 'hsl(var(--foreground))' : 'hsl(var(--muted-foreground))',
                                opacity: action.status === "done" ? 0.7 : 1,
                              }}>
                                {displayName}
                              </span>
                              {action.content && action.status === "done" && (
                                <span className="text-[10px] truncate" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.4, maxWidth: '150px' }}>
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
            <div className={`flex flex-col gap-1.5 ${(ungroupedToolActions.length > 0 || stepGroups.length > 0) ? 'mt-2 pt-2' : ''}`} style={(ungroupedToolActions.length > 0 || stepGroups.length > 0) ? { borderTop: '1px dashed var(--action-border)' } : undefined}>
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

          {isStreaming && (
            <div className="mt-2 h-[2px] w-12 rounded-full overflow-hidden" style={{ background: 'var(--teal-50)' }}>
              <div className="h-full w-1/2 animate-shimmer-line" style={{ background: 'linear-gradient(to right, transparent, var(--teal-400), transparent)' }} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════
   SubAgentCard — nested agent
   ═══════════════════════════════════════ */
function SubAgentCard({ action, isExpanded, onToggle }: { action: ActionDetail; isExpanded: boolean; onToggle: () => void }) {
  const taskLabel = action.delegation?.label || "SubAgent";
  const taskDesc = action.delegation?.task || action.delegation?.skill_name || "";
  const isRunning = action.status === "running";

  return (
    <div
      className="rounded-md overflow-hidden transition-all duration-200"
      style={{
        background: 'var(--thought-bg)',
        border: `1px solid ${isRunning ? 'var(--ocean-200)' : 'var(--thought-border)'}`,
      }}
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-2.5 py-[6px] text-left transition-all duration-200"
        onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ocean-50)'; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
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
          <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="hsl(var(--destructive))" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        )}
        <span className="text-[11px] font-semibold" style={{ color: 'var(--ocean-500)', fontFamily: 'var(--font-mono)' }}>
          {taskLabel}
        </span>
        {taskDesc && (
          <span className="text-[10px] truncate" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.45 }}>
            — {taskDesc}
          </span>
        )}
        <span className="ml-auto">
          <ChevronRight size={11} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.3, transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }} />
        </span>
      </button>
      {isExpanded && (
        <div className="px-2.5 pb-2 pt-0.5 animate-sub-agent-expand" style={{ borderTop: '1px solid var(--thought-border)' }}>
          <div className="pl-3 text-[10px] leading-relaxed" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.55, borderLeft: '2px solid var(--ocean-200)' }}>
            {taskDesc || "子Agent 执行中..."}
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════
   ErrorBlock
   ═══════════════════════════════════════ */
function ErrorBlock({ content }: { content: string }) {
  return (
    <div
      className="w-full px-3 py-2 rounded-lg text-[12px] leading-relaxed"
      style={{
        background: 'rgba(239, 68, 68, 0.06)',
        borderLeft: '2px solid hsl(var(--destructive))',
        color: 'hsl(var(--destructive))',
      }}
    >
      <div className="flex items-center gap-2">
        <AlertCircle size={13} strokeWidth={1.8} style={{ opacity: 0.7 }} />
        <span className="font-medium">{content}</span>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════
   ProcessTimelineView — completed message
   ═══════════════════════════════════════ */
function ProcessTimelineView({ blocks, message, onToggleThought, onUpdateAction }: {
  blocks: MessageBlock[]; message: ChatMessageModel;
  onToggleThought: (messageId: string, blockId: string) => void;
  onUpdateAction?: (callId: string, status: ActionDetail["status"], content: string) => void;
}) {
  let thoughtIdx = 0;
  let actionIdx = 0;
  let stepNum = 1;

  return (
    <div className="flex flex-col gap-1">
      {blocks.map((block) => {
        if (block.type === "thought") {
          const idx = thoughtIdx++;
          const sn = stepNum++;
          return <ThoughtBlock key={block.id} message={message} block={{ ...block, isCollapsed: true }} onToggleThought={onToggleThought} stepIndex={sn} />;
        }
        if (block.type === "action") {
          const idx = actionIdx++;
          const sn = stepNum++;
          return <ActionBlock key={block.id} block={{ ...block, isCollapsed: true }} onToggleThought={onToggleThought} message={message} onUpdateAction={onUpdateAction} stepIndex={sn} />;
        }
        if (block.type === "error") {
          return <ErrorBlock key={block.id} content={block.content} />;
        }
        return (
          <div key={block.id} className="px-3 py-2 rounded-lg text-[11px] leading-relaxed" style={{ background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', color: 'hsl(var(--muted-foreground))', opacity: 0.75 }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.content.length > 200 ? block.content.slice(0, 200) + "…" : block.content}</ReactMarkdown>
          </div>
        );
      })}
    </div>
  );
}

/* ═══════════════════════════════════════
   ImagePreviewDialog
   ═══════════════════════════════════════ */
function ImagePreviewDialog({ artifact, open, onOpenChange }: { artifact: GeneratedArtifact; open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl p-0 overflow-hidden" style={{ background: 'rgba(0,0,0,0.95)' }} showCloseButton={false}>
        <DialogTitle className="sr-only">{artifact.filename}</DialogTitle>
        <DialogDescription className="sr-only">图片预览</DialogDescription>
        <div className="relative flex items-center justify-center min-h-[200px]">
          <img src={artifact.image_url || artifact.download_url} alt={artifact.filename} className="max-w-full max-h-[80vh] object-contain" />
          <div className="absolute top-3 right-3 flex gap-2">
            {artifact.download_url && (
              <a href={artifact.download_url} download={artifact.filename} className="rounded-full bg-white/10 backdrop-blur-sm p-1.5 text-white/80 hover:bg-white/20 transition-colors duration-150" title="下载图片">
                <Download size={16} />
              </a>
            )}
            <button type="button" onClick={() => onOpenChange(false)} className="rounded-full bg-white/10 backdrop-blur-sm p-1.5 text-white/80 hover:bg-white/20 transition-colors duration-150" title="关闭">
              <X size={16} />
            </button>
          </div>
          <div className="absolute bottom-3 left-3 text-white/45 text-[11px] bg-black/20 backdrop-blur-sm px-2 py-0.5 rounded-md">
            {artifact.filename}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ═══════════════════════════════════════
   Artifact rendering helpers
   ═══════════════════════════════════════ */
function ArtifactList({ artifacts, onPreview }: { artifacts: GeneratedArtifact[]; onPreview: (a: GeneratedArtifact) => void }) {
  return (
    <div className="mt-2.5 flex flex-col gap-2.5 items-start">
      {artifacts.map((artifact) =>
        artifact.type === "image_generated" ? (
          <div key={artifact.download_url || artifact.image_url || `${artifact.type}-${artifact.filename}`}
            className="w-[35%] min-w-[200px] overflow-hidden rounded-xl group"
            style={{ border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))' }}
          >
            {artifact.image_url && (
              <div className="relative h-24 w-full overflow-hidden cursor-pointer" style={{ borderBottom: '1px solid hsl(var(--border))' }}
                onClick={() => onPreview(artifact)}
              >
                <img src={artifact.image_url} alt={artifact.filename} className="h-full w-full object-cover" />
                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/12 transition-colors duration-200 flex items-center justify-center">
                  <ZoomIn size={20} className="text-white opacity-0 group-hover:opacity-70 transition-opacity duration-200" />
                </div>
              </div>
            )}
            <div className="px-2.5 py-2 flex items-center justify-between">
              <div className="font-medium truncate flex-1 text-[12px]">{artifact.filename}</div>
              {artifact.download_url && (
                <a href={artifact.download_url} download={artifact.filename}
                  className="ml-1.5 flex-shrink-0 rounded p-1 transition-colors duration-150"
                  style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.45 }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <Download size={13} />
                </a>
              )}
            </div>
          </div>
        ) : (
          <div key={artifact.download_url || `${artifact.type}-${artifact.filename}`}
            className="w-[35%] min-w-[200px] rounded-xl overflow-hidden"
            style={{ border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))' }}
          >
            <div className="px-2.5 py-2 flex items-center gap-2">
              {getFileIcon(artifact.filename)}
              <div className="font-medium truncate flex-1 text-[12px]">{artifact.filename}</div>
            </div>
            <div className="px-2.5 pb-2 flex items-center gap-1">
              {isPreviewable(artifact.filename) && (
                <button type="button" onClick={() => onPreview(artifact)}
                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors duration-150"
                  style={{ color: 'var(--ocean-500)' }}
                >
                  <Eye size={12} /> 预览
                </button>
              )}
              {artifact.download_url && (
                <a href={artifact.download_url} download={artifact.filename}
                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors duration-150"
                  style={{ color: 'hsl(var(--muted-foreground))' }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <Download size={12} /> 下载
                </a>
              )}
            </div>
          </div>
        ),
      )}
    </div>
  );
}

function ReferenceList({ references }: { references: ReferenceLink[] }) {
  return (
    <div className="mt-2.5 pt-2.5" style={{ borderTop: '1px solid hsl(var(--border))', opacity: 0.35 }}>
      <div className="text-[10px] font-semibold mb-1.5" style={{ color: 'hsl(var(--muted-foreground))' }}>参考来源</div>
      <div className="flex flex-col gap-1">
        {references.map((ref, i) => (
          <ReferenceItem key={i} reference={ref} index={i + 1} />
        ))}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════
   Main ChatMessage component
   ═══════════════════════════════════════ */
export function ChatMessage({ message, onToggleThought, onUpdateAction, onQuickSubmit }: ChatMessageProps) {
  const isUser = message.role === "human";
  const isSystem = message.role === "system";
  const [previewArtifact, setPreviewArtifact] = useState<GeneratedArtifact | null>(null);
  const [showProcess, setShowProcess] = useState(false);

  const isComplete = !!message.isComplete;

  const lastAnswerBlock = isComplete
    ? [...message.blocks].reverse().find((b) => b.type === "answer" && b.content.trim())
    : null;

  const processBlocks = isComplete
    ? message.blocks.filter((b) => b !== lastAnswerBlock && b.type !== "error")
    : [];

  const processCount = processBlocks.length;
  const hasProcess = processCount > 0;

  const archivedBlocks = !isComplete ? message.blocks.filter((block) => block.isArchived) : [];
  const archivedToShow = archivedBlocks.slice(-MAX_ARCHIVED_BLOCKS);
  const archivedToShowIds = new Set(archivedToShow.map((block) => block.id));
  const hiddenArchivedCount = Math.max(0, archivedBlocks.length - archivedToShow.length);
  const displayBlocks = !isComplete
    ? message.blocks.filter((block) => !block.isArchived || archivedToShowIds.has(block.id))
    : [];

  let stepNum = 1;

  return (
    <div className={`flex w-full mb-4 animate-message-in ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`flex gap-2.5 max-w-[88%] ${isUser ? "flex-row-reverse" : "flex-row"}`}>
        {/* Avatar */}
        <div className="flex-shrink-0 mt-0.5">
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center"
            style={{
              background: isUser
                ? 'linear-gradient(135deg, var(--slate-600), var(--slate-700))'
                : isSystem
                ? 'linear-gradient(135deg, var(--amber-400), var(--amber-500))'
                : 'var(--ocean-500)',
              boxShadow: isUser
                ? '0 2px 8px rgba(71, 85, 105, 0.18)'
                : isSystem
                ? '0 2px 8px rgba(245, 158, 11, 0.18)'
                : '0 3px 12px rgba(37, 99, 168, 0.25)',
              color: 'white',
            }}
          >
            {isUser ? <User size={14} strokeWidth={1.8} /> : isSystem ? <AlertTriangle size={14} strokeWidth={1.8} /> : <img src="/floodmind-icon.svg" alt="" className="w-4 h-4" style={{ filter: "brightness(0) invert(1)" }} />}
          </div>
        </div>

        <div className={`flex flex-col gap-1.5 ${isUser ? "items-end" : "items-start"}`}>
          {/* User message */}
          {isUser ? (
            <div
              className="px-3.5 py-2.5 rounded-2xl rounded-tr-sm text-[13px] leading-relaxed"
              style={{
                background: 'var(--msg-user-bg)',
                color: 'white',
                boxShadow: '0 2px 10px rgba(37, 99, 168, 0.12)',
              }}
            >
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            </div>
          ) : isSystem ? (
            <div
              className="px-3.5 py-2.5 rounded-2xl rounded-tl-sm text-[13px] leading-relaxed whitespace-pre-wrap font-mono"
              style={{
                background: 'var(--msg-system-bg)',
                border: '1px solid var(--amber-200)',
                color: 'var(--amber-900)',
                opacity: 0.65,
              }}
            >
              {message.content}
            </div>
          ) : (
            <div className="flex flex-col gap-1.5 w-full">
              {/* Archived count badge */}
              {!isComplete && hiddenArchivedCount > 0 && (
                <div className="self-start rounded-md px-2 py-0.5 text-[10px]" style={{ background: 'hsl(var(--muted))', color: 'hsl(var(--muted-foreground))', opacity: 0.5 }}>
                  {hiddenArchivedCount} 步已折叠
                </div>
              )}

              {/* Completed: "查看中间过程" toggle */}
              {isComplete && hasProcess && (
                <button
                  type="button"
                  onClick={() => setShowProcess(!showProcess)}
                  className="flex items-center gap-1.5 self-start rounded-md px-2 py-1 text-[10px] font-medium transition-all duration-200"
                  style={{
                    background: showProcess ? 'var(--thought-bg)' : 'transparent',
                    border: `1px solid ${showProcess ? 'var(--thought-border)' : 'hsl(var(--border))'}`,
                    color: showProcess ? 'var(--ocean-500)' : 'hsl(var(--muted-foreground))',
                    opacity: showProcess ? 1 : 0.5,
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--thought-bg)'; e.currentTarget.style.borderColor = 'var(--thought-border)'; e.currentTarget.style.color = 'var(--ocean-500)'; e.currentTarget.style.opacity = '1'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = showProcess ? 'var(--thought-bg)' : 'transparent'; e.currentTarget.style.borderColor = showProcess ? 'var(--thought-border)' : 'hsl(var(--border))'; e.currentTarget.style.color = showProcess ? 'var(--ocean-500)' : 'hsl(var(--muted-foreground))'; e.currentTarget.style.opacity = showProcess ? '1' : '0.5'; }}
                >
                  <Layers size={10} strokeWidth={1.8} />
                  {showProcess ? "收起过程" : `${processCount} 步过程`}
                  {showProcess ? <ChevronDown size={9} /> : <ChevronRight size={9} />}
                </button>
              )}

              {/* Completed: expanded process view */}
              {isComplete && showProcess && (
                <div className="opacity-60">
                  <ProcessTimelineView blocks={processBlocks} message={message} onToggleThought={onToggleThought} onUpdateAction={onUpdateAction} />
                </div>
              )}

              {/* Streaming blocks */}
              {!isComplete && displayBlocks.map((block) => {
                const isArchived = !!block.isArchived;

                if (block.type === "thought") {
                  const sn = stepNum++;
                  return <ThoughtBlock key={block.id} message={message} block={block} onToggleThought={onToggleThought} stepIndex={sn} />;
                }

                if (block.type === "action") {
                  const sn = stepNum++;
                  return <ActionBlock key={block.id} block={block} onToggleThought={onToggleThought} message={message} onUpdateAction={onUpdateAction} stepIndex={sn} />;
                }

                if (block.type === "error") {
                  return <ErrorBlock key={block.id} content={block.content} />;
                }

                /* Answer block during streaming */
                const displayContent = isArchived && block.isCollapsed && block.content.length > 120
                  ? block.content.slice(0, 120) + "…"
                  : block.content;

                return (
                  <div key={block.id} className={`transition-all duration-300 ${isArchived ? "opacity-40 scale-[0.995]" : "opacity-100"}`}>
                    {/* Answer step header */}
                    <div className="flex items-center gap-2 px-2.5 py-[7px] rounded-lg" style={{ background: 'transparent' }}>
                      <StepBadge index={stepNum} type="answer" />
                      <MessageSquare size={12} style={{ color: 'var(--ocean-400)', opacity: 0.5 }} />
                      <span className="text-[11px] font-semibold" style={{ color: 'var(--ocean-500)' }}>Answer</span>
                    </div>
                    <div
                      className="ml-[26px] mr-1 px-3.5 py-3 rounded-lg text-[13px] leading-relaxed"
                      style={{
                        background: 'var(--gradient-card)',
                        border: '1px solid hsl(var(--border))',
                        color: 'hsl(var(--foreground))',
                        boxShadow: '0 2px 12px rgba(15,31,56,0.04)',
                        backdropFilter: 'blur(6px)',
                      }}
                    >
                      <MarkdownContent content={displayContent} onQuickSubmit={onQuickSubmit} />
                      {!!message.artifacts?.length && block.type === "answer" && !isArchived && (
                        <ArtifactList artifacts={message.artifacts} onPreview={setPreviewArtifact} />
                      )}
                      {!!message.references?.length && message.isComplete && block.type === "answer" && !isArchived && (
                        <ReferenceList references={message.references} />
                      )}
                    </div>
                  </div>
                );
              })}

              {/* Completed: always-visible error blocks */}
              {isComplete && message.blocks.filter((b) => b.type === "error").map((block) => (
                <ErrorBlock key={block.id} content={block.content} />
              ))}

              {/* Completed: final answer */}
              {isComplete && lastAnswerBlock && (
                <div>
                  <div className="flex items-center gap-2 px-2.5 py-[7px] rounded-lg">
                    <StepBadge index={stepNum} type="answer" />
                    <MessageSquare size={12} style={{ color: 'var(--ocean-400)', opacity: 0.5 }} />
                    <span className="text-[11px] font-semibold" style={{ color: 'var(--ocean-500)' }}>Answer</span>
                  </div>
                    <div
                      className="ml-[26px] mr-1 px-3.5 py-3 rounded-lg text-[13px] leading-relaxed"
                      style={{
                        background: 'var(--gradient-card)',
                        border: '1px solid hsl(var(--border))',
                        color: 'hsl(var(--foreground))',
                        boxShadow: '0 2px 12px rgba(15,31,56,0.04)',
                        backdropFilter: 'blur(6px)',
                      }}
                    >
                      <MarkdownContent content={lastAnswerBlock.content} onQuickSubmit={onQuickSubmit} />
                    {!!message.artifacts?.length && (
                      <ArtifactList artifacts={message.artifacts} onPreview={setPreviewArtifact} />
                    )}
                    {!!message.references?.length && (
                      <ReferenceList references={message.references} />
                    )}
                  </div>
                </div>
              )}
              {/* Token usage */}
              {isComplete && message.tokenUsage && message.tokenUsage.total_tokens > 0 && (
                <div className="flex items-center gap-1.5 px-2.5" style={{ opacity: 0.45 }}>
                  <span className="text-[9px] font-mono" style={{ color: 'hsl(var(--muted-foreground))' }}>
                    ↑{message.tokenUsage.prompt_tokens}
                  </span>
                  <span className="text-[9px]" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.4 }}>·</span>
                  <span className="text-[9px] font-mono" style={{ color: 'hsl(var(--muted-foreground))' }}>
                    ↓{message.tokenUsage.completion_tokens}
                  </span>
                  <span className="text-[9px]" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.4 }}>·</span>
                  <span className="text-[9px] font-mono" style={{ color: 'hsl(var(--muted-foreground))' }}>
                    Σ{message.tokenUsage.total_tokens}
                  </span>
                  <span className="text-[9px] ml-0.5" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.3 }}>tokens</span>
                </div>
              )}
            </div>
          )}
          <span className="text-[9px] px-0.5 font-mono" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.3 }}>
            {new Date(message.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
      </div>
      {previewArtifact && previewArtifact.type === "image_generated" && (
        <ImagePreviewDialog artifact={previewArtifact} open={!!previewArtifact} onOpenChange={(open) => { if (!open) setPreviewArtifact(null); }} />
      )}
      {previewArtifact && previewArtifact.type === "file_generated" && (
        <DocumentPreviewDialog artifact={previewArtifact} open={!!previewArtifact} onOpenChange={(open) => { if (!open) setPreviewArtifact(null); }} />
      )}
    </div>
  );
}

/* ═══════════════════════════════════════
   ReferenceItem
   ═══════════════════════════════════════ */
function ReferenceItem({ reference, index }: { reference: ReferenceLink; index: number }) {
  const isWeb = !!reference.url;
  const displayTitle = reference.title.length > 60 ? reference.title.slice(0, 60) + "…" : reference.title;

  if (isWeb) {
    return (
      <a href={reference.url} target="_blank" rel="noopener noreferrer"
        className="flex items-center gap-1.5 px-1.5 py-1 rounded-md text-[11px] transition-all duration-200"
        onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ocean-50)'; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
      >
        <span className="flex-shrink-0 w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold" style={{ background: 'var(--ocean-50)', color: 'var(--ocean-500)' }}>
          {index}
        </span>
        <ExternalLink size={10} className="flex-shrink-0" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35 }} />
        <span className="truncate" style={{ color: 'hsl(var(--muted-foreground))' }}>{displayTitle}</span>
        {reference.source && <span className="flex-shrink-0 text-[9px] ml-auto" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.25 }}>{reference.source}</span>}
      </a>
    );
  }

  return (
    <div className="flex items-center gap-1.5 px-1.5 py-1 rounded-md text-[11px]">
      <span className="flex-shrink-0 w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold" style={{ background: 'var(--ocean-50)', color: 'var(--ocean-500)' }}>
        {index}
      </span>
      <FileText size={10} className="flex-shrink-0" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35 }} />
      <span className="truncate" style={{ color: 'hsl(var(--muted-foreground))' }}>{displayTitle}</span>
      {reference.source && <span className="flex-shrink-0 text-[9px] ml-auto" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.25 }}>{reference.source}</span>}
    </div>
  );
}

/* ─── Markdown with Interactive Task List Support ─── */
function MarkdownContent({ content, onQuickSubmit }: { content: string; onQuickSubmit?: (text: string) => void }) {
  const segments = parseTaskList(content);

  if (segments.length === 0) return null;
  if (segments.every((s) => s.type === "text")) {
    return (
      <div className="prose prose-sm max-w-none" style={{ color: 'hsl(var(--foreground))' }}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    );
  }

  const soloTexts: { index: number; content: string }[] = [];
  const checkboxGroups: { label: string; items: import("@/features/chat/lib/parse-task-list").CheckboxItem[] }[] = [];

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (seg.type === "checkbox") {
      const prevText = i > 0 && segments[i - 1].type === "text" ? segments[i - 1].content : "";
      checkboxGroups.push({ label: prevText, items: seg.items || [] });
    }
  }

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (seg.type === "text") {
      const nextIsCheckbox = i + 1 < segments.length && segments[i + 1].type === "checkbox";
      if (!nextIsCheckbox) {
        soloTexts.push({ index: i, content: seg.content });
      }
    }
  }

  return (
    <div className="flex flex-col gap-1" style={{ color: 'hsl(var(--foreground))' }}>
      {soloTexts.map((t) => (
        <div key={t.index} className="prose prose-sm max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{t.content}</ReactMarkdown>
        </div>
      ))}
      {checkboxGroups.length > 0 && (
        <CheckboxGroupList groups={checkboxGroups} onSubmit={onQuickSubmit} />
      )}
    </div>
  );
}
