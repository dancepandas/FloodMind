import { useState, memo } from "react";
import { MessageSquare } from "lucide-react";
import type { ChatMessage as ChatMessageModel, GeneratedArtifact } from "@/types/app";
import { FileCard } from "./FileCard";
import { DocumentPreviewDialog } from "./DocumentPreviewDialog";
import { StepBadge } from "./blocks/block-primitives";
import { ThoughtBlock } from "./blocks/ThoughtBlock";
import { ActionBlock } from "./blocks/ActionBlock";
import { ErrorBlock } from "./blocks/ErrorBlock";
import { ProcessTimelineView } from "./blocks/ProcessTimelineView";
import { ProcessSummary } from "./blocks/ProcessSummary";
import { ArtifactList, ImagePreviewDialog, ReferenceList } from "./blocks/ArtifactList";
import { MarkdownContent } from "./blocks/MarkdownContent";

interface ChatMessageProps {
  message: ChatMessageModel;
  onToggleThought: (messageId: string, blockId: string) => void;
  onQuickSubmit?: (text: string) => void;
  onPreviewFile?: (fileId: string) => void;
}

const MAX_ARCHIVED_BLOCKS = 4;

// React.memo：updateAssistant 以 prev.map(m => m.id===active ? update(m) : m) 更新，
// 非流式消息保持同一引用；message-blocks 的结构化共享让未变 block 也保持引用。
// 配合 useAgentApp 中已 useCallback 的回调，非活跃消息在 token 流入时跳过重渲染。
const ChatMessageBase = function ChatMessage({ message, onToggleThought, onQuickSubmit, onPreviewFile }: ChatMessageProps) {
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
      <div className={`flex flex-col gap-1.5 max-w-[88%] ${isUser ? "items-end" : "items-start"}`}>
        {/* User message */}
        {isUser ? (
          <>
            <div
              className="px-3.5 py-2.5 rounded-2xl rounded-tr-sm text-[13px] leading-relaxed"
              style={{
                background: "var(--surface-2)",
                color: "var(--text-primary)",
                border: "1px solid var(--border-strong)",
                boxShadow: "var(--shadow)",
              }}
            >
              <MarkdownContent content={message.content} onQuickSubmit={onQuickSubmit} />
            </div>
            {message.attachments && message.attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 justify-end max-w-full">
                {message.attachments.map((file) => (
                  <FileCard key={file.id} file={file} onClick={onPreviewFile ? () => onPreviewFile(file.id) : undefined} />
                ))}
              </div>
            )}
          </>
        ) : isSystem ? (
          <div
            className="px-3.5 py-2.5 rounded-2xl rounded-tl-sm text-[13px] leading-relaxed whitespace-pre-wrap font-mono"
            style={{
              background: "var(--surface-2)",
              border: "1px solid var(--sand)",
              color: "var(--sand)",
              opacity: 0.8,
            }}
          >
            {message.content}
          </div>
        ) : (
          <div className="flex flex-col gap-1.5 w-full">
            {/* Archived count badge */}
            {!isComplete && hiddenArchivedCount > 0 && (
              <div className="self-start rounded-md px-2 py-0.5 text-[10px]" style={{ background: "var(--surface-2)", color: "var(--text-tertiary)" }}>
                {hiddenArchivedCount} 步已折叠
              </div>
            )}

            {/* Completed: 过程折叠入口（统一 ProcessSummary） */}
            {isComplete && hasProcess && (
              <ProcessSummary
                stepCount={processBlocks.filter((b) => b.type === "thought" || b.type === "action").length}
                thoughtCount={processBlocks.filter((b) => b.type === "thought").length}
                actionCount={processBlocks.filter((b) => b.type === "action").length}
                isStreaming={false}
                isExpanded={showProcess}
                onToggle={() => setShowProcess(!showProcess)}
              />
            )}

            {/* Completed: expanded process view */}
            {isComplete && showProcess && (
              <div className="opacity-70">
                <ProcessTimelineView blocks={processBlocks} message={message} onToggleThought={onToggleThought} />
              </div>
            )}

            {/* Streaming: 过程折叠（thought/action 聚合到 ProcessSummary）+ 回答突出（CC 风） */}
            {!isComplete && (() => {
              const streamingProcess = displayBlocks.filter((b) => b.type === "thought" || b.type === "action");
              const streamingAnswer = displayBlocks.filter((b) => b.type === "answer");
              const streamingError = displayBlocks.filter((b) => b.type === "error");
              const sThought = streamingProcess.filter((b) => b.type === "thought").length;
              const sAction = streamingProcess.filter((b) => b.type === "action").length;
              let procSn = 1;

              return (
                <>
                  {/* 流式错误即时显示 */}
                  {streamingError.map((b) => (
                    <ErrorBlock key={b.id} content={b.content} />
                  ))}

                  {/* 过程折叠入口（CC 风：过程低权重，默认折叠；点击展开看思考/工具详情） */}
                  {streamingProcess.length > 0 && (
                    <ProcessSummary
                      stepCount={streamingProcess.length}
                      thoughtCount={sThought}
                      actionCount={sAction}
                      isStreaming={true}
                      isExpanded={showProcess}
                      onToggle={() => setShowProcess(!showProcess)}
                    />
                  )}
                  {showProcess && streamingProcess.length > 0 && (
                    <div className="opacity-70 flex flex-col gap-1.5">
                      {streamingProcess.map((block) => {
                        if (block.type === "thought") {
                          const sn = procSn++;
                          return <ThoughtBlock key={block.id} message={message} block={block} onToggleThought={onToggleThought} stepIndex={sn} />;
                        }
                        const sn = procSn++;
                        return <ActionBlock key={block.id} block={block} onToggleThought={onToggleThought} message={message} stepIndex={sn} />;
                      })}
                    </div>
                  )}

                  {/* 回答块突出（主内容，实时展开） */}
                  {streamingAnswer.map((block) => {
                    const isArchived = !!block.isArchived;
                    const displayContent = isArchived && block.isCollapsed && block.content.length > 120
                      ? block.content.slice(0, 120) + "…"
                      : block.content;
                    return (
                      <div key={block.id} className={`transition-all duration-300 ${isArchived ? "opacity-40 scale-[0.995]" : "opacity-100"}`}>
                        <div className="flex items-center gap-2 px-2.5 py-[7px] rounded-lg" style={{ background: "transparent" }}>
                          <StepBadge index={stepNum} type="answer" />
                          <MessageSquare size={12} style={{ color: "var(--wave)", opacity: 0.5 }} />
                          <span className="text-[11px] font-semibold" style={{ color: "var(--wave)" }}>回答</span>
                        </div>
                        <div
                          className="ml-[26px] mr-1 px-3.5 py-3 rounded-lg text-[13px] leading-relaxed"
                          style={{
                            background: "var(--surface)",
                            border: "1px solid var(--border)",
                            color: "var(--text-primary)",
                            boxShadow: "var(--shadow)",
                          }}
                        >
                          <MarkdownContent content={displayContent} onQuickSubmit={onQuickSubmit} />
                          {!!message.artifacts?.length && !isArchived && (
                            <ArtifactList artifacts={message.artifacts} onPreview={setPreviewArtifact} />
                          )}
                          {!!message.references?.length && message.isComplete && !isArchived && (
                            <ReferenceList references={message.references} />
                          )}
                        </div>
                      </div>
                    );
                  })}
                </>
              );
            })()}

            {/* Completed: always-visible error blocks */}
            {isComplete && message.blocks.filter((b) => b.type === "error").map((block) => (
              <ErrorBlock key={block.id} content={block.content} />
            ))}

            {/* Completed: final answer */}
            {isComplete && lastAnswerBlock && (
              <div>
                <div className="flex items-center gap-2 px-2.5 py-[7px] rounded-lg">
                  <StepBadge index={stepNum} type="answer" />
                  <MessageSquare size={12} style={{ color: "var(--wave)", opacity: 0.5 }} />
                  <span className="text-[11px] font-semibold" style={{ color: "var(--wave)" }}>Answer</span>
                </div>
                  <div
                    className="ml-[26px] mr-1 px-3.5 py-3 rounded-lg text-[13px] leading-relaxed"
                    style={{
                      background: "var(--surface)",
                      border: "1px solid var(--border)",
                      color: "var(--text-primary)",
                      boxShadow: "var(--shadow)",
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
              <div className="flex items-center gap-1.5 px-2.5">
                <span className="text-[9px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                  ↑{message.tokenUsage.prompt_tokens}
                </span>
                <span className="text-[9px]" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>·</span>
                <span className="text-[9px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                  ↓{message.tokenUsage.completion_tokens}
                </span>
                <span className="text-[9px]" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>·</span>
                <span className="text-[9px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                  Σ{message.tokenUsage.total_tokens}
                </span>
                <span className="text-[9px] ml-0.5" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>tokens</span>
              </div>
            )}
          </div>
        )}
        <span className="text-[9px] px-0.5 font-mono" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>
          {new Date(message.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </span>
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

export const ChatMessage = memo(ChatMessageBase);
