import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Bot, User, Download, X, ZoomIn, Terminal, ExternalLink, FileText, Eye, Loader2, CheckCircle2, XCircle, Wrench } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage as ChatMessageModel, GeneratedArtifact, ReferenceLink, ActionDetail } from "@/types/app";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { DocumentPreviewDialog, isPreviewable, getFileExt, getFileIcon } from "./DocumentPreviewDialog";
import { getToolDisplayName } from "@/features/chat/lib/message-blocks";

interface ChatMessageProps {
  message: ChatMessageModel;
  onToggleThought: (messageId: string, blockId: string) => void;
}

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
    <div className={`w-full max-w-full transition-all duration-300 ${block.isArchived ? "opacity-30" : "opacity-100"}`}>
      <button
        type="button"
        onClick={() => onToggleThought(message.id, block.id)}
        className="flex items-center gap-2.5 text-[11px] font-semibold tracking-wide text-muted-foreground/70 hover:text-foreground transition-colors duration-200 px-1.5 py-1.5 rounded-md hover:bg-muted/30"
      >
        {block.isCollapsed ? <ChevronRight size={12} strokeWidth={2} /> : <ChevronDown size={12} strokeWidth={2} />}
        <div className={`relative flex items-center justify-center ${block.isStreaming ? "" : ""}`}>
          <span className={`h-[6px] w-[6px] rounded-full ${block.isStreaming ? "bg-primary shadow-[0_0_6px_rgba(59,107,208,0.5)]" : "bg-primary/50"}`} />
          {block.isStreaming && <span className="absolute h-[6px] w-[6px] rounded-full bg-primary/40 animate-ping" />}
        </div>
        <span className="uppercase">{block.isStreaming ? "Thinking" : "Thought"}</span>
      </button>
      <div
        ref={scrollRef}
        className={`mt-1 ml-3 pl-4 pr-3 py-2.5 rounded-r-lg text-[12px] text-muted-foreground/70 leading-relaxed transition-all duration-300 overflow-x-hidden overflow-y-auto ${block.isCollapsed ? "max-h-0 opacity-0 py-0 mt-0" : "max-h-40 opacity-100"}`}
        style={{
          background: "linear-gradient(135deg, rgba(59,107,208,0.04) 0%, rgba(59,107,208,0.01) 100%)",
          borderLeft: "2px solid rgba(59,107,208,0.25)",
          boxShadow: "inset 2px 0 6px -4px rgba(59,107,208,0.08), 0 1px 2px -1px rgba(0,0,0,0.03)",
          borderTop: "1px solid rgba(59,107,208,0.06)",
          borderRight: "1px solid rgba(59,107,208,0.04)",
          borderBottom: "1px solid rgba(59,107,208,0.04)",
        }}
      >
        <div ref={contentRef} className="whitespace-pre-wrap break-words max-w-[65ch]">{block.content}</div>
      </div>
    </div>
  );
}

function ActionStatusIcon({ status }: { status: ActionDetail["status"] }) {
  if (status === "running") {
    return (
      <span className="relative flex items-center justify-center w-3.5 h-3.5">
        <span className="absolute w-3.5 h-3.5 rounded-full bg-primary/20 animate-ping" />
        <span className="w-2 h-2 rounded-full bg-primary shadow-[0_0_4px_rgba(59,107,208,0.4)]" />
      </span>
    );
  }
  if (status === "done") {
    return <CheckCircle2 size={13} className="text-emerald-500" strokeWidth={2} />;
  }
  return <XCircle size={13} className="text-red-400" strokeWidth={2} />;
}

function ActionBlock({ block, onToggleThought, message }: { block: ChatMessageModel["blocks"][number]; onToggleThought: (messageId: string, blockId: string) => void; message: ChatMessageModel }) {
  const actions = block.actions || [];
  const isStreaming = block.isStreaming;
  const isCollapsed = block.isCollapsed;
  const isArchived = block.isArchived;

  const runningCount = actions.filter((a) => a.status === "running").length;
  const doneCount = actions.filter((a) => a.status === "done").length;
  const errorCount = actions.filter((a) => a.status === "error").length;

  const headerLabel = isStreaming
    ? `执行中${runningCount > 0 ? ` · ${runningCount}项进行中` : ""}${doneCount > 0 ? ` · ${doneCount}项完成` : ""}`
    : isArchived
      ? `已执行 ${actions.length} 项操作`
      : `${actions.length} 项操作`;

  return (
    <div className={`w-full max-w-full transition-all duration-300 ${isArchived ? "opacity-35" : "opacity-100"}`}>
      <button
        type="button"
        onClick={() => onToggleThought(message.id, block.id)}
        className="flex items-center gap-2.5 text-[11px] font-semibold tracking-wide text-muted-foreground/70 hover:text-foreground transition-colors duration-200 px-1.5 py-1.5 rounded-md hover:bg-muted/30"
      >
        {isCollapsed ? <ChevronRight size={12} strokeWidth={2} /> : <ChevronDown size={12} strokeWidth={2} />}
        {isStreaming ? (
          <span className="relative flex items-center justify-center">
            <span className="h-[6px] w-[6px] rounded-full bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.5)]" />
            <span className="absolute h-[6px] w-[6px] rounded-full bg-emerald-500/40 animate-ping" />
          </span>
        ) : (
          <Wrench size={11} className="text-muted-foreground/50" strokeWidth={2} />
        )}
        <span className="uppercase">{headerLabel}</span>
      </button>
      <div
        className={`mt-1 ml-3 pl-4 pr-3 py-2.5 rounded-r-lg text-[12px] leading-relaxed transition-all duration-300 overflow-hidden ${isCollapsed ? "max-h-0 opacity-0 py-0 mt-0" : "max-h-60 opacity-100 overflow-y-auto"}`}
        style={{
          background: "linear-gradient(135deg, rgba(16,185,129,0.04) 0%, rgba(16,185,129,0.01) 100%)",
          borderLeft: "2px solid rgba(16,185,129,0.25)",
          boxShadow: "inset 2px 0 6px -4px rgba(16,185,129,0.08), 0 1px 2px -1px rgba(0,0,0,0.03)",
          borderTop: "1px solid rgba(16,185,129,0.06)",
          borderRight: "1px solid rgba(16,185,129,0.04)",
          borderBottom: "1px solid rgba(16,185,129,0.04)",
        }}
      >
        <div className="flex flex-col gap-2">
          {actions.map((action, idx) => (
            <div key={idx} className="flex flex-col">
              <div className="flex items-center gap-2.5 py-0.5">
                <ActionStatusIcon status={action.status} />
                <span className={`text-[12px] font-medium tracking-tight ${action.status === "running" ? "text-foreground" : action.status === "done" ? "text-muted-foreground/80" : "text-red-400"}`}>
                  {action.delegation?.label || getToolDisplayName(action.toolName)}
                </span>
                {action.status === "running" && (
                  <span className="text-[10px] text-muted-foreground/45 font-medium">running</span>
                )}
              </div>
              {action.status === "done" && action.delegation?.summary && (
                <div className="ml-6 text-[11px] text-muted-foreground/50 max-h-16 overflow-y-auto whitespace-pre-wrap break-all leading-relaxed">
                  {action.delegation.summary.slice(0, 300)}
                </div>
              )}
              {action.status === "error" && action.content && (
                <div className="ml-6 text-[11px] text-red-400/70 truncate max-w-[400px]">
                  {action.content.slice(0, 80).replace(/\n/g, " ")}
                </div>
              )}
            </div>
          ))}
        </div>
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
                className="rounded-full bg-white/15 backdrop-blur-sm p-2 text-white/90 hover:bg-white/25 transition-colors duration-150"
                title="下载图片"
              >
                <Download size={17} />
              </a>
            )}
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="rounded-full bg-white/15 backdrop-blur-sm p-2 text-white/90 hover:bg-white/25 transition-colors duration-150"
              title="关闭"
            >
              <X size={17} />
            </button>
          </div>
          <div className="absolute bottom-3 left-3 text-white/60 text-xs bg-black/30 backdrop-blur-sm px-2.5 py-1 rounded-lg">
            {artifact.filename}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ChatMessage({ message, onToggleThought }: ChatMessageProps) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const [previewArtifact, setPreviewArtifact] = useState<GeneratedArtifact | null>(null);

  return (
    <div className={`flex w-full mb-6 ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`flex gap-3 max-w-[85%] ${isUser ? "flex-row-reverse" : "flex-row"}`}>
        <div className="flex-shrink-0 mt-0.5">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center shadow-[0_2px_8px_-2px_rgba(0,0,0,0.06)] ${isUser ? "bg-primary/10 text-primary" : isSystem ? "bg-amber-50 text-amber-600" : "bg-gradient-to-br from-primary/12 to-primary/4 text-primary"}`}>
            {isUser ? <User size={15} strokeWidth={1.8} /> : isSystem ? <Terminal size={15} strokeWidth={1.8} /> : <img src="/floodmind-icon.svg" alt="FloodMind" className="w-4.5 h-4.5" />}
          </div>
        </div>

        <div className={`flex flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}>
          {isUser ? (
            <div className="px-4 py-2.5 rounded-2xl rounded-tr-md bg-primary text-primary-foreground shadow-[0_2px_8px_-2px_rgba(59,107,208,0.15)] text-sm whitespace-pre-wrap leading-relaxed">
              {message.content}
            </div>
          ) : isSystem ? (
            <div className="px-4 py-3 rounded-2xl rounded-tl-md bg-amber-50/80 border border-amber-200/60 shadow-sm text-sm text-amber-900 leading-relaxed whitespace-pre-wrap font-mono">
              {message.content}
            </div>
          ) : (
            <div className="flex flex-col gap-3 w-full">
              {message.blocks.filter((block) => !block.isArchived).map((block) => {
                if (block.type === "thought") {
                  return <ThoughtBlock key={block.id} message={message} block={block} onToggleThought={onToggleThought} />;
                }

                if (block.type === "action") {
                  return <ActionBlock key={block.id} block={block} onToggleThought={onToggleThought} message={message} />;
                }

                return (
                  <div key={block.id} className={`px-4 py-3 rounded-2xl rounded-tl-md bg-card border border-border/60 text-sm text-foreground leading-relaxed transition-all duration-300 shadow-[0_1px_4px_-1px_rgba(0,0,0,0.03)] ${block.isArchived ? "opacity-50 scale-[0.99]" : "opacity-100"}`}>
                    <div className="prose prose-sm max-w-none prose-headings:text-foreground prose-p:text-foreground prose-strong:text-foreground prose-li:text-foreground prose-code:text-foreground prose-code:bg-muted prose-code:px-1 prose-code:rounded prose-table:text-foreground prose-th:bg-muted prose-th:border-border prose-td:border-border">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.content}</ReactMarkdown>
                    </div>
                    {!!message.artifacts?.length && message.blocks[message.blocks.length - 1].id === block.id && (
                      <div className="mt-3 flex flex-col gap-3 items-start">
                        {message.artifacts.map((artifact) =>
                          artifact.type === "image_generated" ? (
                            <div
                              key={artifact.download_url || artifact.image_url || `${artifact.type}-${artifact.filename}`}
                              className="w-[33%] min-w-[220px] overflow-hidden rounded-xl border border-border/80 bg-card text-sm hover:border-primary/30 transition-all duration-200 group"
                            >
                              {artifact.image_url && (
                                <div
                                  className="relative h-28 w-full overflow-hidden border-b border-border/60 cursor-pointer"
                                  onClick={() => setPreviewArtifact(artifact)}
                                >
                                  <img
                                    src={artifact.image_url}
                                    alt={artifact.filename}
                                    className="h-full w-full object-cover"
                                  />
                                  <div className="absolute inset-0 bg-black/0 group-hover:bg-black/15 transition-colors duration-200 flex items-center justify-center">
                                    <ZoomIn size={22} className="text-white opacity-0 group-hover:opacity-80 transition-opacity duration-200" />
                                  </div>
                                </div>
                              )}
                              <div className="px-3 py-2.5 flex items-center justify-between">
                                <div className="font-medium truncate flex-1 text-[13px]">{artifact.filename}</div>
                                {artifact.download_url && (
                                  <a
                                    href={artifact.download_url}
                                    download={artifact.filename}
                                    className="ml-2 flex-shrink-0 rounded-lg p-1.5 text-muted-foreground/60 hover:text-primary hover:bg-primary/5 transition-colors duration-150"
                                    title="下载图片"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <Download size={14} />
                                  </a>
                                )}
                              </div>
                            </div>
                          ) : (
                            <div
                              key={artifact.download_url || `${artifact.type}-${artifact.filename}`}
                              className="w-[33%] min-w-[220px] rounded-xl border border-border/80 bg-card text-sm hover:border-primary/30 transition-all duration-200 overflow-hidden"
                            >
                              <div className="px-3 py-2.5 flex items-center gap-2.5">
                                {getFileIcon(artifact.filename)}
                                <div className="font-medium truncate flex-1 text-[13px]">{artifact.filename}</div>
                              </div>
                              <div className="px-3 pb-2.5 flex items-center gap-1.5">
                                {isPreviewable(artifact.filename) && (
                                  <button
                                    type="button"
                                    onClick={() => setPreviewArtifact(artifact)}
                                    className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-primary hover:bg-primary/5 transition-colors duration-150"
                                  >
                                    <Eye size={13} />
                                    预览
                                  </button>
                                )}
                                {artifact.download_url && (
                                  <a
                                    href={artifact.download_url}
                                    download={artifact.filename}
                                    className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors duration-150"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <Download size={13} />
                                    下载
                                  </a>
                                )}
                              </div>
                            </div>
                          ),
                        )}
                      </div>
                    )}
                    {!!message.references?.length && message.blocks[message.blocks.length - 1].id === block.id && (
                      <div className="mt-3 pt-3 border-t border-border/50">
                        <div className="text-[11px] font-medium text-muted-foreground mb-2">参考来源</div>
                        <div className="flex flex-col gap-1.5">
                          {message.references.map((ref, i) => (
                            <ReferenceItem key={i} reference={ref} index={i + 1} />
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          <span className="text-[10px] text-muted-foreground/50 px-1">
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
        className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs hover:bg-muted/40 transition-colors duration-150 group"
      >
        <span className="flex-shrink-0 w-5 h-5 rounded-md bg-primary/8 text-primary flex items-center justify-center text-[10px] font-semibold">
          {index}
        </span>
        <ExternalLink size={11} className="flex-shrink-0 text-muted-foreground/50 group-hover:text-primary transition-colors duration-150" />
        <span className="truncate text-muted-foreground group-hover:text-foreground transition-colors duration-150">{displayTitle}</span>
        {reference.source && <span className="flex-shrink-0 text-[10px] text-muted-foreground/40 ml-auto">{reference.source}</span>}
      </a>
    );
  }

  return (
    <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs">
      <span className="flex-shrink-0 w-5 h-5 rounded-md bg-primary/8 text-primary flex items-center justify-center text-[10px] font-semibold">
        {index}
      </span>
      <FileText size={11} className="flex-shrink-0 text-muted-foreground/50" />
      <span className="truncate text-muted-foreground">{displayTitle}</span>
      {reference.source && <span className="flex-shrink-0 text-[10px] text-muted-foreground/40 ml-auto">{reference.source}</span>}
    </div>
  );
}
