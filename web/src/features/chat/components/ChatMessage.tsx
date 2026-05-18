import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Bot, User, Download, X, ZoomIn, Terminal, ExternalLink, FileText, Eye, Loader2, CheckCircle2, XCircle, Wrench, ShieldAlert, Layers } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage as ChatMessageModel, GeneratedArtifact, ReferenceLink, ActionDetail } from "@/types/app";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { DocumentPreviewDialog, isPreviewable, getFileExt, getFileIcon } from "./DocumentPreviewDialog";
import { getToolDisplayName } from "@/features/chat/lib/message-blocks";

function SparkleIcon({ size = 12, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
      <path d="M12 0L13.5 8.5L22 6L15 12L22 18L13.5 15.5L12 24L10.5 15.5L2 18L9 12L2 6L10.5 8.5L12 0Z" fill="currentColor" />
    </svg>
  );
}

interface ChatMessageProps {
  message: ChatMessageModel;
  onToggleThought: (messageId: string, blockId: string) => void;
  onUpdateAction?: (callId: string, status: ActionDetail["status"], content: string) => void;
}

const MAX_ARCHIVED_BLOCKS = 4;

function ThoughtBlock({ message, block, onToggleThought }: { message: ChatMessageModel; block: ChatMessageModel["blocks"][number]; onToggleThought: (messageId: string, blockId: string) => void }) {
  const contentRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (block.isStreaming && !block.isCollapsed && scrollRef.current) {
      const el = scrollRef.current;
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
  }, [block.content, block.isStreaming, block.isCollapsed]);

  return (
    <div className={`w-full max-w-full transition-all duration-300 ${block.isArchived ? "opacity-50" : "opacity-100"}`}>
      <button
        type="button"
        onClick={() => onToggleThought(message.id, block.id)}
        className="flex items-center gap-2 text-[10px] font-semibold tracking-wider text-muted-foreground/55 hover:text-foreground transition-colors duration-200 px-1 py-1 rounded-md hover:bg-muted/20"
      >
        {block.isCollapsed ? <ChevronRight size={11} strokeWidth={2} /> : <ChevronDown size={11} strokeWidth={2} />}
        {block.isStreaming ? (
          <span className="relative flex items-center justify-center w-3.5 h-3.5">
            <SparkleIcon size={14} className="text-sky-500 animate-star-spin-breathe" />
            <span className="absolute inset-0 rounded-full bg-sky-400/15 animate-ping" />
          </span>
        ) : (
          <SparkleIcon size={11} className="text-primary/40" />
        )}
        <span className="uppercase">{block.isStreaming ? "Thinking" : "Thought"}</span>
      </button>
      <div
        ref={scrollRef}
        className={`mt-1 ml-2.5 pl-3.5 pr-3 py-2.5 rounded-xl text-[11px] text-muted-foreground/80 leading-relaxed transition-all duration-300 overflow-x-hidden overflow-y-auto ${block.isCollapsed ? "max-h-0 opacity-0 py-0 mt-0" : "max-h-36 opacity-100"}`}
        style={{
          background: "linear-gradient(135deg, rgba(14,165,233,0.05) 0%, rgba(14,165,233,0.015) 100%)",
          border: "1px solid rgba(14,165,233,0.1)",
        }}
      >
        <div ref={contentRef} className="whitespace-pre-wrap break-words max-w-[65ch]">{block.isArchived && block.isCollapsed && block.content.length > 200 ? block.content.slice(0, 200) + "…" : block.content}</div>
        {block.isStreaming && (
          <div className="mt-1.5 h-[2px] w-16 rounded-full overflow-hidden bg-sky-100">
            <div className="h-full w-1/2 bg-gradient-to-r from-transparent via-sky-400 to-transparent animate-shimmer-line" />
          </div>
        )}
      </div>
    </div>
  );
}

function ActionStatusIcon({ status }: { status: ActionDetail["status"] }) {
  if (status === "running") {
    return (
      <span className="relative flex items-center justify-center w-3.5 h-3.5">
        <SparkleIcon size={12} className="text-sky-500 animate-star-spin-breathe" />
      </span>
    );
  }
  if (status === "pending_confirmation") {
    return <ShieldAlert size={12} className="text-amber-500" strokeWidth={2} />;
  }
  if (status === "done") {
    return <CheckCircle2 size={12} className="text-emerald-500" strokeWidth={2} />;
  }
  return <XCircle size={12} className="text-red-400" strokeWidth={2} />;
}

function ActionBlock({ block, onToggleThought, message, onUpdateAction }: { block: ChatMessageModel["blocks"][number]; onToggleThought: (messageId: string, blockId: string) => void; message: ChatMessageModel; onUpdateAction?: (callId: string, status: ActionDetail["status"], content: string) => void }) {
  const actions = block.actions || [];
  const isStreaming = block.isStreaming;
  const isCollapsed = block.isCollapsed;
  const isArchived = block.isArchived;

  const runningCount = actions.filter((a) => a.status === "running" || a.status === "pending_confirmation").length;
  const doneCount = actions.filter((a) => a.status === "done").length;

  const headerLabel = isStreaming
    ? `执行中${runningCount > 0 ? ` · ${runningCount}项进行中` : ""}${doneCount > 0 ? ` · ${doneCount}项完成` : ""}`
    : isArchived
      ? `已执行 ${actions.length} 项操作`
      : `${actions.length} 项操作`;

  return (
    <div className={`w-full max-w-full transition-all duration-300 ${isArchived ? "opacity-55" : "opacity-100"}`}>
      <button
        type="button"
        onClick={() => onToggleThought(message.id, block.id)}
        className="flex items-center gap-2 text-[10px] font-semibold tracking-wider text-muted-foreground/75 hover:text-foreground transition-colors duration-200 px-1 py-1 rounded-md hover:bg-muted/20"
      >
        {isCollapsed ? <ChevronRight size={11} strokeWidth={2} /> : <ChevronDown size={11} strokeWidth={2} />}
        {isStreaming ? (
          <span className="relative flex items-center justify-center w-3.5 h-3.5">
            <SparkleIcon size={14} className="text-emerald-500 animate-star-spin-breathe" />
            <span className="absolute inset-0 rounded-full bg-emerald-400/12 animate-ping" />
          </span>
        ) : (
          <Wrench size={10} className="text-muted-foreground/40" strokeWidth={2} />
        )}
        <span className="uppercase">{headerLabel}</span>
      </button>
      <div
        className={`mt-1 ml-2.5 pl-3.5 pr-3 py-2.5 rounded-xl text-[11px] leading-relaxed transition-all duration-300 overflow-hidden ${isCollapsed ? "max-h-0 opacity-0 py-0 mt-0" : "max-h-52 opacity-100 overflow-y-auto"}`}
        style={{
          background: "linear-gradient(135deg, rgba(16,185,129,0.05) 0%, rgba(16,185,129,0.015) 100%)",
          border: "1px solid rgba(16,185,129,0.1)",
        }}
      >
        <div className="flex flex-col gap-1.5">
          {actions.map((action) => (
            <div key={action.callId || action.toolName} className="flex flex-col">
              <div className="flex items-center gap-2 py-0.5">
                <ActionStatusIcon status={action.status} />
                <span className={`text-[11px] font-medium tracking-tight ${action.status === "running" ? "text-foreground" : action.status === "done" ? "text-muted-foreground/70" : "text-red-400"}`}>
                  {action.delegation?.label || getToolDisplayName(action.toolName)}
                </span>
                {action.status === "running" && (
                  <span className="text-[9px] text-muted-foreground/35 font-medium">running</span>
                )}
              </div>
              {action.status === "done" && action.delegation?.summary && (
                <div className="ml-5 text-[10px] text-muted-foreground/45 max-h-14 overflow-y-auto whitespace-pre-wrap break-all leading-relaxed">
                  {action.delegation.summary.slice(0, 300)}
                </div>
              )}
              {action.status === "error" && action.content && (
                <div className="ml-5 text-[10px] text-red-400/60 truncate max-w-[380px]">
                  {action.content.slice(0, 80).replace(/\n/g, " ")}
                </div>
              )}
              {action.status === "pending_confirmation" && (
                <div className="ml-5 mt-0.5">
                  {action.askReason && (
                    <div className="text-[10px] text-amber-600/70 max-w-[380px]">
                      {action.askReason}
                    </div>
                  )}
                  <span className="text-[9px] text-amber-500/50">等待确认…</span>
                </div>
              )}
            </div>
          ))}
        </div>
        {isStreaming && (
          <div className="mt-2 h-[2px] w-16 rounded-full overflow-hidden bg-emerald-50">
            <div className="h-full w-1/2 bg-gradient-to-r from-transparent via-emerald-400 to-transparent animate-shimmer-line" />
          </div>
        )}
      </div>
    </div>
  );
}

function ImagePreviewDialog({ artifact, open, onOpenChange }: { artifact: GeneratedArtifact; open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl p-0 overflow-hidden bg-black/95 border-border" showCloseButton={false}>
        <DialogTitle className="sr-only">{artifact.filename}</DialogTitle>
        <DialogDescription className="sr-only">图片预览</DialogDescription>
        <div className="relative flex items-center justify-center min-h-[200px]">
          <img
            src={artifact.image_url || artifact.download_url}
            alt={artifact.filename}
            className="max-w-full max-h-[80vh] object-contain"
          />
          <div className="absolute top-3 right-3 flex gap-2">
            {artifact.download_url && (
              <a
                href={artifact.download_url}
                download={artifact.filename}
                className="rounded-full bg-white/12 backdrop-blur-sm p-1.5 text-white/85 hover:bg-white/22 transition-colors duration-150"
                title="下载图片"
              >
                <Download size={16} />
              </a>
            )}
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="rounded-full bg-white/12 backdrop-blur-sm p-1.5 text-white/85 hover:bg-white/22 transition-colors duration-150"
              title="关闭"
            >
              <X size={16} />
            </button>
          </div>
          <div className="absolute bottom-3 left-3 text-white/50 text-[11px] bg-black/25 backdrop-blur-sm px-2 py-0.5 rounded-md">
            {artifact.filename}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ChatMessage({ message, onToggleThought, onUpdateAction }: ChatMessageProps) {
  const isUser = message.role === "human";
  const isSystem = message.role === "system";
  const [previewArtifact, setPreviewArtifact] = useState<GeneratedArtifact | null>(null);
  const [showProcess, setShowProcess] = useState(false);

  const isComplete = !!message.isComplete;

  const lastAnswerBlock = isComplete
    ? [...message.blocks].reverse().find((b) => b.type === "answer" && b.content.trim())
    : null;

  const processBlocks = isComplete
    ? message.blocks.filter((b) => b !== lastAnswerBlock)
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

  return (
    <div className={`flex w-full mb-5 animate-message-in ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`flex gap-2.5 max-w-[88%] ${isUser ? "flex-row-reverse" : "flex-row"}`}>
        <div className="flex-shrink-0 mt-0.5">
          <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${isUser ? "bg-primary/8 text-primary" : isSystem ? "bg-amber-50 text-amber-600" : "bg-sky-500 text-white shadow-[0_2px_6px_-2px_rgba(14,165,233,0.25)]"}`}>
            {isUser ? <User size={14} strokeWidth={1.8} /> : isSystem ? <Terminal size={14} strokeWidth={1.8} /> : <img src="/floodmind-icon.svg" alt="FloodMind" className="w-4 h-4" style={{ filter: "brightness(0) invert(1)" }} />}
          </div>
        </div>

        <div className={`flex flex-col gap-1.5 ${isUser ? "items-end" : "items-start"}`}>
          {isUser ? (
            <div className="px-3.5 py-2 rounded-2xl rounded-tr-sm bg-primary text-primary-foreground shadow-[0_1px_6px_-2px_rgba(14,165,233,0.15)] text-[13px] whitespace-pre-wrap leading-relaxed">
              {message.content}
            </div>
          ) : isSystem ? (
            <div className="px-3.5 py-2.5 rounded-2xl rounded-tl-sm bg-amber-50/70 border border-amber-200/50 text-[13px] text-amber-900 leading-relaxed whitespace-pre-wrap font-mono">
              {message.content}
            </div>
          ) : (
            <div className="flex flex-col gap-2.5 w-full">
              {!isComplete && hiddenArchivedCount > 0 && (
                <div className="self-start rounded-full border border-border/30 bg-muted/20 px-2.5 py-1 text-[10px] text-muted-foreground/45">
                  已折叠 {hiddenArchivedCount} 个较早中间步骤
                </div>
              )}

              {isComplete && hasProcess && (
                <button
                  type="button"
                  onClick={() => setShowProcess(!showProcess)}
                  className="flex items-center gap-1.5 self-start rounded-md border border-border/30 bg-muted/15 px-2.5 py-1 text-[10px] text-muted-foreground/50 hover:text-muted-foreground/70 hover:bg-muted/25 transition-all duration-200"
                >
                  <Layers size={10} strokeWidth={1.8} />
                  {showProcess ? "收起中间过程" : `查看中间过程（${processCount}项）`}
                  {showProcess ? <ChevronDown size={9} /> : <ChevronRight size={9} />}
                </button>
              )}

              {isComplete && showProcess && (
                <div className="flex flex-col gap-1.5 opacity-70">
                  {processBlocks.map((block) => {
                    if (block.type === "thought") {
                      return <ThoughtBlock key={block.id} message={message} block={{ ...block, isCollapsed: true }} onToggleThought={onToggleThought} />;
                    }
                    if (block.type === "action") {
                      return <ActionBlock key={block.id} block={{ ...block, isCollapsed: true }} onToggleThought={onToggleThought} message={message} onUpdateAction={onUpdateAction} />;
                    }
                    return (
                      <div key={block.id} className="px-3 py-2 rounded-xl bg-card border border-border/30 text-[11px] text-muted-foreground/80 leading-relaxed">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.content.length > 200 ? block.content.slice(0, 200) + "…" : block.content}</ReactMarkdown>
                      </div>
                    );
                  })}
                </div>
              )}

              {!isComplete && displayBlocks.map((block) => {
                const isArchived = !!block.isArchived;
                if (block.type === "thought") {
                  return <ThoughtBlock key={block.id} message={message} block={block} onToggleThought={onToggleThought} />;
                }

                if (block.type === "action") {
                  return <ActionBlock key={block.id} block={block} onToggleThought={onToggleThought} message={message} onUpdateAction={onUpdateAction} />;
                }

                const displayContent = isArchived && block.isCollapsed && block.content.length > 120
                  ? block.content.slice(0, 120) + "…"
                  : block.content;

                return (
                  <div key={block.id} className={`px-4 py-3 rounded-2xl rounded-tl-sm bg-card border border-border/40 text-[13px] text-foreground leading-relaxed transition-all duration-300 shadow-[0_1px_4px_-1px_rgba(0,0,0,0.02)] ${isArchived ? "opacity-40 scale-[0.995]" : "opacity-100"}`}>
                    <div className="prose prose-sm max-w-none prose-headings:text-foreground prose-p:text-foreground prose-strong:text-foreground prose-li:text-foreground prose-code:text-foreground prose-code:bg-muted prose-code:px-1 prose-code:rounded prose-table:text-foreground prose-th:bg-muted prose-th:border-border prose-td:border-border">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayContent}</ReactMarkdown>
                    </div>
                    {!!message.artifacts?.length && block.type === "answer" && !isArchived && (
                      <div className="mt-2.5 flex flex-col gap-2.5 items-start">
                        {message.artifacts.map((artifact) =>
                          artifact.type === "image_generated" ? (
                            <div
                              key={artifact.download_url || artifact.image_url || `${artifact.type}-${artifact.filename}`}
                              className="w-[35%] min-w-[200px] overflow-hidden rounded-xl border border-border/60 bg-card text-sm hover:border-primary/25 transition-all duration-200 group"
                            >
                              {artifact.image_url && (
                                <div
                                  className="relative h-24 w-full overflow-hidden border-b border-border/40 cursor-pointer"
                                  onClick={() => setPreviewArtifact(artifact)}
                                >
                                  <img
                                    src={artifact.image_url}
                                    alt={artifact.filename}
                                    className="h-full w-full object-cover"
                                  />
                                  <div className="absolute inset-0 bg-black/0 group-hover:bg-black/12 transition-colors duration-200 flex items-center justify-center">
                                    <ZoomIn size={20} className="text-white opacity-0 group-hover:opacity-70 transition-opacity duration-200" />
                                  </div>
                                </div>
                              )}
                              <div className="px-2.5 py-2 flex items-center justify-between">
                                <div className="font-medium truncate flex-1 text-[12px]">{artifact.filename}</div>
                                {artifact.download_url && (
                                  <a
                                    href={artifact.download_url}
                                    download={artifact.filename}
                                    className="ml-1.5 flex-shrink-0 rounded p-1 text-muted-foreground/50 hover:text-primary hover:bg-primary/5 transition-colors duration-150"
                                    title="下载图片"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <Download size={13} />
                                  </a>
                                )}
                              </div>
                            </div>
                          ) : (
                            <div
                              key={artifact.download_url || `${artifact.type}-${artifact.filename}`}
                              className="w-[35%] min-w-[200px] rounded-xl border border-border/60 bg-card text-sm hover:border-primary/25 transition-all duration-200 overflow-hidden"
                            >
                              <div className="px-2.5 py-2 flex items-center gap-2">
                                {getFileIcon(artifact.filename)}
                                <div className="font-medium truncate flex-1 text-[12px]">{artifact.filename}</div>
                              </div>
                              <div className="px-2.5 pb-2 flex items-center gap-1">
                                {isPreviewable(artifact.filename) && (
                                  <button
                                    type="button"
                                    onClick={() => setPreviewArtifact(artifact)}
                                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-primary hover:bg-primary/5 transition-colors duration-150"
                                  >
                                    <Eye size={12} />
                                    预览
                                  </button>
                                )}
                                {artifact.download_url && (
                                  <a
                                    href={artifact.download_url}
                                    download={artifact.filename}
                                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors duration-150"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <Download size={12} />
                                    下载
                                  </a>
                                )}
                              </div>
                            </div>
                          ),
                        )}
                      </div>
                    )}
                    {!!message.references?.length && message.isComplete && block.type === "answer" && !isArchived && (
                      <div className="mt-2.5 pt-2.5 border-t border-border/35">
                        <div className="text-[10px] font-semibold text-muted-foreground/50 mb-1.5">参考来源</div>
                        <div className="flex flex-col gap-1">
                          {message.references.map((ref, i) => (
                            <ReferenceItem key={i} reference={ref} index={i + 1} />
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}

              {isComplete && lastAnswerBlock && (
                <div className="px-4 py-3 rounded-2xl rounded-tl-sm bg-card border border-border/40 text-[13px] text-foreground leading-relaxed shadow-[0_1px_4px_-1px_rgba(0,0,0,0.02)]">
                  <div className="prose prose-sm max-w-none prose-headings:text-foreground prose-p:text-foreground prose-strong:text-foreground prose-li:text-foreground prose-code:text-foreground prose-code:bg-muted prose-code:px-1 prose-code:rounded prose-table:text-foreground prose-th:bg-muted prose-th:border-border prose-td:border-border">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{lastAnswerBlock.content}</ReactMarkdown>
                  </div>
                  {!!message.artifacts?.length && (
                    <div className="mt-2.5 flex flex-col gap-2.5 items-start">
                      {message.artifacts.map((artifact) =>
                        artifact.type === "image_generated" ? (
                          <div
                            key={artifact.download_url || artifact.image_url || `${artifact.type}-${artifact.filename}`}
                            className="w-[35%] min-w-[200px] overflow-hidden rounded-xl border border-border/60 bg-card text-sm hover:border-primary/25 transition-all duration-200 group"
                          >
                            {artifact.image_url && (
                              <div
                                className="relative h-24 w-full overflow-hidden border-b border-border/40 cursor-pointer"
                                onClick={() => setPreviewArtifact(artifact)}
                              >
                                <img
                                  src={artifact.image_url}
                                  alt={artifact.filename}
                                  className="h-full w-full object-cover"
                                />
                                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/12 transition-colors duration-200 flex items-center justify-center">
                                  <ZoomIn size={20} className="text-white opacity-0 group-hover:opacity-70 transition-opacity duration-200" />
                                </div>
                              </div>
                            )}
                            <div className="px-2.5 py-2 flex items-center justify-between">
                              <div className="font-medium truncate flex-1 text-[12px]">{artifact.filename}</div>
                              {artifact.download_url && (
                                <a
                                  href={artifact.download_url}
                                  download={artifact.filename}
                                  className="ml-1.5 flex-shrink-0 rounded p-1 text-muted-foreground/50 hover:text-primary hover:bg-primary/5 transition-colors duration-150"
                                  title="下载图片"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <Download size={13} />
                                </a>
                              )}
                            </div>
                          </div>
                        ) : (
                          <div
                            key={artifact.download_url || `${artifact.type}-${artifact.filename}`}
                            className="w-[35%] min-w-[200px] rounded-xl border border-border/60 bg-card text-sm hover:border-primary/25 transition-all duration-200 overflow-hidden"
                          >
                            <div className="px-2.5 py-2 flex items-center gap-2">
                              {getFileIcon(artifact.filename)}
                              <div className="font-medium truncate flex-1 text-[12px]">{artifact.filename}</div>
                            </div>
                            <div className="px-2.5 pb-2 flex items-center gap-1">
                              {isPreviewable(artifact.filename) && (
                                <button
                                  type="button"
                                  onClick={() => setPreviewArtifact(artifact)}
                                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-primary hover:bg-primary/5 transition-colors duration-150"
                                >
                                  <Eye size={12} />
                                  预览
                                </button>
                              )}
                              {artifact.download_url && (
                                <a
                                  href={artifact.download_url}
                                  download={artifact.filename}
                                  className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors duration-150"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <Download size={12} />
                                  下载
                                </a>
                              )}
                            </div>
                          </div>
                        ),
                      )}
                    </div>
                  )}
                  {!!message.references?.length && (
                    <div className="mt-2.5 pt-2.5 border-t border-border/35">
                      <div className="text-[10px] font-semibold text-muted-foreground/50 mb-1.5">参考来源</div>
                      <div className="flex flex-col gap-1">
                        {message.references.map((ref, i) => (
                          <ReferenceItem key={i} reference={ref} index={i + 1} />
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          <span className="text-[9px] text-muted-foreground/35 px-0.5 font-mono">
            {new Date(message.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </span>
        </div>
      </div>
      {previewArtifact && previewArtifact.type === "image_generated" && (
        <ImagePreviewDialog
          artifact={previewArtifact}
          open={!!previewArtifact}
          onOpenChange={(open) => { if (!open) setPreviewArtifact(null); }}
        />
      )}
      {previewArtifact && previewArtifact.type === "file_generated" && (
        <DocumentPreviewDialog
          artifact={previewArtifact}
          open={!!previewArtifact}
          onOpenChange={(open) => { if (!open) setPreviewArtifact(null); }}
        />
      )}
    </div>
  );
}

function ReferenceItem({ reference, index }: { reference: ReferenceLink; index: number }) {
  const isWeb = !!reference.url;
  const displayTitle = reference.title.length > 60 ? reference.title.slice(0, 60) + "…" : reference.title;

  if (isWeb) {
    return (
      <a
        href={reference.url}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-1.5 px-1.5 py-1 rounded-md text-[11px] hover:bg-muted/30 transition-colors duration-150 group"
      >
        <span className="flex-shrink-0 w-4 h-4 rounded bg-primary/6 text-primary flex items-center justify-center text-[9px] font-semibold">
          {index}
        </span>
        <ExternalLink size={10} className="flex-shrink-0 text-muted-foreground/40 group-hover:text-primary transition-colors duration-150" />
        <span className="truncate text-muted-foreground group-hover:text-foreground transition-colors duration-150">{displayTitle}</span>
        {reference.source && <span className="flex-shrink-0 text-[9px] text-muted-foreground/30 ml-auto">{reference.source}</span>}
      </a>
    );
  }

  return (
    <div className="flex items-center gap-1.5 px-1.5 py-1 rounded-md text-[11px]">
      <span className="flex-shrink-0 w-4 h-4 rounded bg-primary/6 text-primary flex items-center justify-center text-[9px] font-semibold">
        {index}
      </span>
      <FileText size={10} className="flex-shrink-0 text-muted-foreground/40" />
      <span className="truncate text-muted-foreground">{displayTitle}</span>
      {reference.source && <span className="flex-shrink-0 text-[9px] text-muted-foreground/30 ml-auto">{reference.source}</span>}
    </div>
  );
}
