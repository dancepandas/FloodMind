import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Bot, User, Download, X, ZoomIn, Terminal, ExternalLink, FileText, Eye } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage as ChatMessageModel, GeneratedArtifact, ReferenceLink } from "@/types/app";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { DocumentPreviewDialog, isPreviewable, getFileExt, getFileIcon } from "./DocumentPreviewDialog";

interface ChatMessageProps {
  message: ChatMessageModel;
  onToggleThought: (messageId: string, blockId: string) => void;
}

function ThoughtBlock({ message, block, onToggleThought }: { message: ChatMessageModel; block: ChatMessageModel["blocks"][number]; onToggleThought: (messageId: string, blockId: string) => void }) {
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (block.isStreaming && !block.isCollapsed && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [block.content, block.isStreaming, block.isCollapsed]);

  return (
    <div className={`w-full transition-all duration-300 ${block.isArchived ? "opacity-40" : "opacity-100"}`}>
      <button
        type="button"
        onClick={() => onToggleThought(message.id, block.id)}
        className="flex items-center gap-2 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors px-2 py-1.5 rounded-md hover:bg-muted/50"
      >
        {block.isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${block.isStreaming ? "bg-primary animate-pulse" : "bg-primary/70"}`} />
        <span>{block.isStreaming ? "Thinking..." : "Thought Process"}</span>
      </button>
      <div className={`mt-1 ml-2 pl-4 pr-3 py-2.5 border-l-2 border-primary/30 bg-muted/20 rounded-r-md text-sm text-muted-foreground whitespace-pre-wrap leading-relaxed transition-all duration-300 overflow-hidden ${block.isCollapsed ? "max-h-0 opacity-0 py-0 mt-0" : "max-h-32 overflow-y-auto opacity-100"}`}>
        <div ref={contentRef} className="opacity-70">{block.content}</div>
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
                className="rounded-full bg-white/20 backdrop-blur-sm p-2 text-white hover:bg-white/30 transition-colors"
                title="下载图片"
              >
                <Download size={18} />
              </a>
            )}
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="rounded-full bg-white/20 backdrop-blur-sm p-2 text-white hover:bg-white/30 transition-colors"
              title="关闭"
            >
              <X size={18} />
            </button>
          </div>
          <div className="absolute bottom-3 left-3 text-white/70 text-xs bg-black/40 backdrop-blur-sm px-2 py-1 rounded">
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
      <div className={`flex gap-4 max-w-[85%] ${isUser ? "flex-row-reverse" : "flex-row"}`}>
        <div className="flex-shrink-0 mt-1">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center shadow-sm ${isUser ? "bg-primary text-primary-foreground" : isSystem ? "bg-amber-100 text-amber-700 border border-amber-200" : "bg-muted text-foreground border border-border"}`}>
            {isUser ? <User size={16} /> : isSystem ? <Terminal size={16} /> : <Bot size={18} />}
          </div>
        </div>

        <div className={`flex flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}>
          {isUser ? (
            <div className="px-4 py-2.5 rounded-2xl rounded-tr-sm bg-primary text-primary-foreground shadow-sm text-sm whitespace-pre-wrap">
              {message.content}
            </div>
          ) : isSystem ? (
            <div className="px-4 py-3 rounded-2xl rounded-tl-sm bg-amber-50 border border-amber-200 shadow-sm text-sm text-amber-900 leading-relaxed whitespace-pre-wrap font-mono">
              {message.content}
            </div>
          ) : (
            <div className="flex flex-col gap-3 w-full">
              {message.blocks.filter((block) => block.type === "thought" || !block.isArchived).map((block) => {
                if (block.type === "thought") {
                  return <ThoughtBlock key={block.id} message={message} block={block} onToggleThought={onToggleThought} />;
                }

                return (
                  <div key={block.id} className={`px-4 py-3 rounded-2xl rounded-tl-sm bg-background border border-border shadow-sm text-sm text-foreground leading-relaxed transition-all duration-300 ${block.isArchived ? "opacity-50 scale-[0.99]" : "opacity-100"}`}>
                    <div className="prose prose-sm max-w-none prose-headings:text-foreground prose-p:text-foreground prose-strong:text-foreground prose-li:text-foreground prose-code:text-foreground prose-code:bg-muted prose-code:px-1 prose-code:rounded prose-table:text-foreground prose-th:bg-muted prose-th:border-border prose-td:border-border">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.content}</ReactMarkdown>
                    </div>
                    {!!message.artifacts?.length && message.blocks[message.blocks.length - 1].id === block.id && (
                      <div className="mt-3 flex flex-col gap-3 items-start">
                        {message.artifacts.map((artifact) =>
                          artifact.type === "image_generated" ? (
                            <div
                              key={`${artifact.filepath}-${artifact.filename}`}
                              className="w-[33%] min-w-[220px] overflow-hidden rounded-xl border border-border bg-muted/30 text-sm shadow-sm hover:border-primary/40 transition-colors group"
                            >
                              {artifact.image_url && (
                                <div
                                  className="relative h-28 w-full overflow-hidden border-b border-border cursor-pointer"
                                  onClick={() => setPreviewArtifact(artifact)}
                                >
                                  <img
                                    src={artifact.image_url}
                                    alt={artifact.filename}
                                    className="h-full w-full object-cover"
                                  />
                                  <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors flex items-center justify-center">
                                    <ZoomIn size={24} className="text-white opacity-0 group-hover:opacity-80 transition-opacity" />
                                  </div>
                                </div>
                              )}
                              <div className="px-3 py-3 flex items-center justify-between">
                                <div className="font-medium truncate flex-1">{artifact.filename}</div>
                                {artifact.download_url && (
                                  <a
                                    href={artifact.download_url}
                                    download={artifact.filename}
                                    className="ml-2 flex-shrink-0 rounded-md p-1.5 text-muted-foreground hover:text-primary hover:bg-muted transition-colors"
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
                              key={`${artifact.filepath}-${artifact.filename}`}
                              className="w-[33%] min-w-[220px] rounded-xl border border-border bg-muted/30 text-sm shadow-sm hover:border-primary/40 transition-colors overflow-hidden"
                            >
                              <div className="px-3 py-3 flex items-center gap-2.5">
                                {getFileIcon(artifact.filename)}
                                <div className="font-medium truncate flex-1">{artifact.filename}</div>
                              </div>
                              <div className="px-3 pb-2.5 flex items-center gap-1.5">
                                {isPreviewable(artifact.filename) && (
                                  <button
                                    type="button"
                                    onClick={() => setPreviewArtifact(artifact)}
                                    className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-primary hover:bg-primary/10 transition-colors"
                                  >
                                    <Eye size={13} />
                                    预览
                                  </button>
                                )}
                                {artifact.download_url && (
                                  <a
                                    href={artifact.download_url}
                                    download={artifact.filename}
                                    className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
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
                      <div className="mt-3 pt-3 border-t border-border/60">
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
          <span className="text-[10px] text-muted-foreground px-1">
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
        className="flex items-center gap-2 px-2 py-1.5 rounded-md text-xs hover:bg-muted/60 transition-colors group"
      >
        <span className="flex-shrink-0 w-5 h-5 rounded bg-primary/10 text-primary flex items-center justify-center text-[10px] font-semibold">
          {index}
        </span>
        <ExternalLink size={12} className="flex-shrink-0 text-muted-foreground group-hover:text-primary transition-colors" />
        <span className="truncate text-muted-foreground group-hover:text-foreground transition-colors">{displayTitle}</span>
        {reference.source && <span className="flex-shrink-0 text-[10px] text-muted-foreground/60 ml-auto">{reference.source}</span>}
      </a>
    );
  }

  return (
    <div className="flex items-center gap-2 px-2 py-1.5 rounded-md text-xs">
      <span className="flex-shrink-0 w-5 h-5 rounded bg-primary/10 text-primary flex items-center justify-center text-[10px] font-semibold">
        {index}
      </span>
      <FileText size={12} className="flex-shrink-0 text-muted-foreground" />
      <span className="truncate text-muted-foreground">{displayTitle}</span>
      {reference.source && <span className="flex-shrink-0 text-[10px] text-muted-foreground/60 ml-auto">{reference.source}</span>}
    </div>
  );
}
