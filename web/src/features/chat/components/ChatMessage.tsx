import { useState } from "react";
import { ChevronDown, ChevronRight, User, FileText, AlertTriangle, Eye, Download, ZoomIn, ExternalLink, Layers, X, AlertCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage as ChatMessageModel, GeneratedArtifact, ReferenceLink, ActionDetail, MessageBlock } from "@/types/app";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { DocumentPreviewDialog, isPreviewable, getFileExt, getFileIcon } from "./DocumentPreviewDialog";
import { getToolDisplayName } from "@/features/chat/lib/message-blocks";

function SparkleIcon({ size = 12, className = "", style }: { size?: number; className?: string; style?: React.CSSProperties }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} style={style}>
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
  return (
    <div className={`w-full max-w-full transition-all duration-300 ${block.isArchived ? "opacity-50" : "opacity-100"}`}>
      <button
        type="button"
        onClick={() => onToggleThought(message.id, block.id)}
        className="flex items-center gap-2 text-[10px] font-semibold tracking-wider transition-all duration-200 px-1.5 py-1 rounded-lg"
        style={{ color: 'var(--ocean-400)' }}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ocean-50)'; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
      >
        {block.isCollapsed ? <ChevronRight size={11} strokeWidth={2} /> : <ChevronDown size={11} strokeWidth={2} />}
        {block.isStreaming ? (
          <span className="relative flex items-center justify-center w-3.5 h-3.5">
            <SparkleIcon size={14} className="animate-star-spin-breathe" style={{ color: 'var(--ocean-500)' }} />
            <span className="absolute inset-0 rounded-full animate-ping" style={{ background: 'var(--ocean-400)', opacity: 0.12 }} />
          </span>
        ) : (
          <SparkleIcon size={11} style={{ color: 'var(--ocean-400)', opacity: 0.35 }} />
        )}
        <span className="uppercase">{block.isStreaming ? "Thinking" : "Thought"}</span>
      </button>
      <div
        className={`mt-1.5 ml-3 pl-4 pr-3 py-2.5 rounded-xl text-[11px] leading-relaxed transition-all duration-400 overflow-x-hidden overflow-y-auto ${block.isCollapsed ? "max-h-0 opacity-0 py-0 mt-0" : "max-h-36 opacity-100"}`}
        style={{
          background: 'var(--thought-bg)',
          border: '1px solid var(--thought-border)',
          color: 'hsl(var(--muted-foreground))',
          opacity: block.isCollapsed ? 0 : 0.75,
          backdropFilter: 'blur(4px)',
        }}
      >
        <div className="whitespace-pre-wrap break-words max-w-[65ch]">{block.content}</div>
        {block.isStreaming && (
          <div className="mt-1.5 h-[2px] w-16 rounded-full overflow-hidden" style={{ background: 'var(--ocean-100)' }}>
            <div className="h-full w-1/2 animate-shimmer-line" style={{ background: 'linear-gradient(to right, transparent, var(--ocean-400), transparent)' }} />
          </div>
        )}
      </div>
    </div>
  );
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
        className="flex items-center gap-2 text-[10px] font-semibold tracking-wider transition-all duration-200 px-1.5 py-1 rounded-lg"
        style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.7 }}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'hsl(var(--muted))'; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
      >
        {isCollapsed ? <ChevronRight size={11} strokeWidth={2} /> : <ChevronDown size={11} strokeWidth={2} />}
        {isStreaming ? (
          <span className="relative flex items-center justify-center w-3.5 h-3.5">
            <SparkleIcon size={14} className="animate-star-spin-breathe" style={{ color: 'var(--teal-500)' }} />
            <span className="absolute inset-0 rounded-full animate-ping" style={{ background: 'var(--teal-400)', opacity: 0.1 }} />
          </span>
        ) : (
          <SparkleIcon size={10} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35 }} />
        )}
        <span className="uppercase">{headerLabel}</span>
      </button>
      <div
        className={`mt-1.5 ml-3 pl-4 pr-3 py-2.5 rounded-xl text-[11px] leading-relaxed transition-all duration-400 overflow-hidden ${isCollapsed ? "max-h-0 opacity-0 py-0 mt-0" : "max-h-52 opacity-100 overflow-y-auto"}`}
        style={{
          background: 'var(--action-bg)',
          border: '1px solid var(--action-border)',
          backdropFilter: 'blur(4px)',
        }}
      >
        <div className="flex flex-wrap gap-x-3 gap-y-1.5">
          {actions.map((action) => {
            const isSubAgent = action.toolName === 'SubAgent' || action.toolName === 'ParallelSubAgent';
            const displayName = isSubAgent ? 'SubAgent' : getToolDisplayName(action.toolName);
            return (
              <div key={action.callId || action.toolName} className="flex items-center gap-1.5">
                {action.status === "running" ? (
                  <span className="relative flex items-center justify-center w-3 h-3">
                    <SparkleIcon size={10} className="animate-star-spin-breathe" style={{ color: 'var(--teal-500)' }} />
                  </span>
                ) : action.status === "done" ? (
                  <SparkleIcon size={8} style={{ color: 'var(--teal-500)', opacity: 0.5 }} />
                ) : (
                  <SparkleIcon size={8} style={{ color: 'hsl(var(--destructive))', opacity: 0.6 }} />
                )}
                <span className="text-[11px] font-medium tracking-tight"
                  style={{
                    color: action.status === "running" ? 'hsl(var(--foreground))'
                      : isSubAgent ? 'var(--ocean-500)'
                      : 'hsl(var(--muted-foreground))',
                    opacity: action.status === "done" ? 0.65 : 1,
                  }}
                >
                  {displayName}
                </span>
              </div>
            );
          })}
        </div>
        {isStreaming && (
          <div className="mt-2 h-[2px] w-16 rounded-full overflow-hidden" style={{ background: 'var(--teal-50)' }}>
            <div className="h-full w-1/2 animate-shimmer-line" style={{ background: 'linear-gradient(to right, transparent, var(--teal-400), transparent)' }} />
          </div>
        )}
      </div>
    </div>
  );
}

function ErrorBlock({ content }: { content: string }) {
  return (
    <div
      className="w-full max-w-full px-4 py-3 rounded-2xl rounded-tl-sm text-[13px] leading-relaxed"
      style={{
        background: 'rgba(239, 68, 68, 0.06)',
        border: '1px solid rgba(239, 68, 68, 0.18)',
        color: 'hsl(var(--destructive))',
        backdropFilter: 'blur(6px)',
      }}
    >
      <div className="flex items-center gap-2">
        <AlertCircle size={15} strokeWidth={1.8} style={{ opacity: 0.7 }} />
        <span className="font-medium">{content}</span>
      </div>
    </div>
  );
}

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
          {isUser ? (
            <div
              className="px-3.5 py-2.5 rounded-2xl rounded-tr-sm text-[13px] whitespace-pre-wrap leading-relaxed"
              style={{
                background: 'var(--msg-user-bg)',
                color: 'white',
                boxShadow: '0 2px 10px rgba(37, 99, 168, 0.12)',
              }}
            >
              {message.content}
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
            <div className="flex flex-col gap-2.5 w-full">
              {!isComplete && hiddenArchivedCount > 0 && (
                <div className="self-start rounded-full px-2.5 py-1 text-[10px]" style={{ border: '1px solid hsl(var(--border))', background: 'hsl(var(--muted))', color: 'hsl(var(--muted-foreground))', opacity: 0.4 }}>
                  已折叠 {hiddenArchivedCount} 个较早中间步骤
                </div>
              )}

              {isComplete && hasProcess && (
                <button
                  type="button"
                  onClick={() => setShowProcess(!showProcess)}
                  className="flex items-center gap-1.5 self-start rounded-lg px-2.5 py-1 text-[10px] transition-all duration-200"
                  style={{ border: '1px solid hsl(var(--border))', background: 'hsl(var(--muted))', color: 'hsl(var(--muted-foreground))', opacity: 0.45 }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ocean-50)'; e.currentTarget.style.borderColor = 'var(--ocean-200)'; e.currentTarget.style.color = 'var(--ocean-500)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'hsl(var(--muted))'; e.currentTarget.style.borderColor = 'hsl(var(--border))'; e.currentTarget.style.color = 'hsl(var(--muted-foreground))'; e.currentTarget.style.opacity = '0.45'; }}
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
                    if (block.type === "error") {
                      return <ErrorBlock key={block.id} content={block.content} />;
                    }
                    return (
                      <div key={block.id} className="px-3 py-2 rounded-xl text-[11px] leading-relaxed" style={{ background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))', color: 'hsl(var(--muted-foreground))', opacity: 0.75 }}>
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

                if (block.type === "error") {
                  return <ErrorBlock key={block.id} content={block.content} />;
                }

                const displayContent = isArchived && block.isCollapsed && block.content.length > 120
                  ? block.content.slice(0, 120) + "…"
                  : block.content;

                return (
                  <div key={block.id} className={`px-4 py-3 rounded-2xl rounded-tl-sm text-[13px] leading-relaxed transition-all duration-300 ${isArchived ? "opacity-40 scale-[0.995]" : "opacity-100"}`}
                    style={{
                      background: 'var(--gradient-card)',
                      border: '1px solid hsl(var(--border))',
                      color: 'hsl(var(--foreground))',
                      boxShadow: '0 2px 12px rgba(15,31,56,0.04)',
                      backdropFilter: 'blur(6px)',
                    }}
                  >
                    <div className="prose prose-sm max-w-none" style={{ color: 'hsl(var(--foreground))' }}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayContent}</ReactMarkdown>
                    </div>
                    {!!message.artifacts?.length && block.type === "answer" && !isArchived && (
                      <div className="mt-2.5 flex flex-col gap-2.5 items-start">
                        {message.artifacts.map((artifact) =>
                          artifact.type === "image_generated" ? (
                            <div key={artifact.download_url || artifact.image_url || `${artifact.type}-${artifact.filename}`}
                              className="w-[35%] min-w-[200px] overflow-hidden rounded-xl group"
                              style={{ border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))' }}
                            >
                              {artifact.image_url && (
                                <div className="relative h-24 w-full overflow-hidden cursor-pointer" style={{ borderBottom: '1px solid hsl(var(--border))' }}
                                  onClick={() => setPreviewArtifact(artifact)}
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
                                  <button type="button" onClick={() => setPreviewArtifact(artifact)}
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
                    )}
                    {!!message.references?.length && message.isComplete && block.type === "answer" && !isArchived && (
                      <div className="mt-2.5 pt-2.5" style={{ borderTop: '1px solid hsl(var(--border))', opacity: 0.35 }}>
                        <div className="text-[10px] font-semibold mb-1.5" style={{ color: 'hsl(var(--muted-foreground))' }}>参考来源</div>
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
                <div className="px-4 py-3 rounded-2xl rounded-tl-sm text-[13px] leading-relaxed"
                  style={{
                    background: 'var(--gradient-card)',
                    border: '1px solid hsl(var(--border))',
                    color: 'hsl(var(--foreground))',
                    boxShadow: '0 2px 12px rgba(15,31,56,0.04)',
                    backdropFilter: 'blur(6px)',
                  }}
                >
                  <div className="prose prose-sm max-w-none" style={{ color: 'hsl(var(--foreground))' }}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{lastAnswerBlock.content}</ReactMarkdown>
                  </div>
                  {!!message.artifacts?.length && (
                    <div className="mt-2.5 flex flex-col gap-2.5 items-start">
                      {message.artifacts.map((artifact) =>
                        artifact.type === "image_generated" ? (
                          <div key={artifact.download_url || artifact.image_url || `${artifact.type}-${artifact.filename}`}
                            className="w-[35%] min-w-[200px] overflow-hidden rounded-xl group"
                            style={{ border: '1px solid hsl(var(--border))', background: 'hsl(var(--card))' }}
                          >
                            {artifact.image_url && (
                              <div className="relative h-24 w-full overflow-hidden cursor-pointer" style={{ borderBottom: '1px solid hsl(var(--border))' }}
                                onClick={() => setPreviewArtifact(artifact)}
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
                                <button type="button" onClick={() => setPreviewArtifact(artifact)}
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
                  )}
                  {!!message.references?.length && (
                    <div className="mt-2.5 pt-2.5" style={{ borderTop: '1px solid hsl(var(--border))', opacity: 0.35 }}>
                      <div className="text-[10px] font-semibold mb-1.5" style={{ color: 'hsl(var(--muted-foreground))' }}>参考来源</div>
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

function ReferenceItem({ reference, index }: { reference: ReferenceLink; index: number }) {
  const isWeb = !!reference.url;
  const displayTitle = reference.title.length > 60 ? reference.title.slice(0, 60) + "…" : reference.title;

  if (isWeb) {
    return (
      <a href={reference.url} target="_blank" rel="noopener noreferrer"
        className="flex items-center gap-1.5 px-1.5 py-1 rounded-md text-[11px] transition-all duration-200 group"
        onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--ocean-50)'; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
      >
        <span className="flex-shrink-0 w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold" style={{ background: 'var(--ocean-50)', color: 'var(--ocean-500)' }}>
          {index}
        </span>
        <ExternalLink size={10} className="flex-shrink-0 transition-colors duration-150" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35 }} />
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