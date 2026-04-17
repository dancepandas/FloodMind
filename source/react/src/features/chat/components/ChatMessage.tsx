import { ChevronDown, ChevronRight, Bot, User } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage as ChatMessageModel } from "@/types/app";

interface ChatMessageProps {
  message: ChatMessageModel;
  onToggleThought: (messageId: string, blockId: string) => void;
}

export function ChatMessage({ message, onToggleThought }: ChatMessageProps) {
  const isUser = message.role === "user";

  return (
    <div className={`flex w-full mb-6 ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`flex gap-4 max-w-[85%] ${isUser ? "flex-row-reverse" : "flex-row"}`}>
        <div className="flex-shrink-0 mt-1">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center shadow-sm ${isUser ? "bg-primary text-primary-foreground" : "bg-muted text-foreground border border-border"}`}>
            {isUser ? <User size={16} /> : <Bot size={18} />}
          </div>
        </div>

        <div className={`flex flex-col gap-2 ${isUser ? "items-end" : "items-start"}`}>
          {isUser ? (
            <div className="px-4 py-2.5 rounded-2xl rounded-tr-sm bg-primary text-primary-foreground shadow-sm text-sm whitespace-pre-wrap">
              {message.content}
            </div>
          ) : (
            <div className="flex flex-col gap-3 w-full">
              {message.blocks.filter((block) => block.type === "thought" || !block.isArchived).map((block) => {
                if (block.type === "thought") {
                  return (
                    <div key={block.id} className={`w-full transition-all duration-300 ${block.isArchived ? "opacity-40" : "opacity-100"}`}>
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
                        <div className="opacity-70">{block.content}</div>
                      </div>
                    </div>
                  );
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
                            <a
                              key={`${artifact.filepath}-${artifact.filename}`}
                              href={artifact.image_url || artifact.download_url || "#"}
                              target="_blank"
                              rel="noreferrer"
                              className="w-[33%] min-w-[220px] overflow-hidden rounded-xl border border-border bg-muted/30 text-sm shadow-sm hover:border-primary/40 transition-colors"
                            >
                              {artifact.image_url ? (
                                <img src={artifact.image_url} alt={artifact.filename} className="h-28 w-full object-cover border-b border-border" />
                              ) : null}
                              <div className="px-3 py-3">
                                <div className="font-medium truncate">{artifact.filename}</div>
                                <div className="text-xs text-muted-foreground mt-1">查看 / 下载图片</div>
                              </div>
                            </a>
                          ) : (
                            <a
                              key={`${artifact.filepath}-${artifact.filename}`}
                              href={artifact.download_url || "#"}
                              download={artifact.filename}
                              className="w-[33%] min-w-[220px] rounded-xl border border-border bg-muted/30 px-3 py-3 text-sm shadow-sm hover:border-primary/40 transition-colors"
                            >
                              <div className="font-medium truncate">{artifact.filename}</div>
                              <div className="text-xs text-muted-foreground mt-1">下载文件</div>
                            </a>
                          ),
                        )}
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
    </div>
  );
}
