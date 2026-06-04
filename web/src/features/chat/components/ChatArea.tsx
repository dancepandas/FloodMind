import { useEffect, useRef, useState } from "react";
import { ChatComposer } from "@/features/chat/components/ChatComposer";
import { ChatMessage } from "@/features/chat/components/ChatMessage";
import WelcomePage from "@/components/WelcomePage";
import { useIsMobile } from "@/hooks/use-mobile";
import { FileText, X, ChevronDown, ChevronRight, ListTree } from "lucide-react";
import { FileCard, formatFileSize } from "@/features/chat/components/FileCard";
import type { ChatMessage as ChatMessageModel, ModelOption, SessionConfig, ActionDetail, PendingPermissionAsk, UploadedFileItem, WorkflowPlan, FilePreview } from "@/types/app";

interface ChatAreaProps {
  messages: ChatMessageModel[];
  inputValue: string;
  isStreaming: boolean;
  isReconnecting: boolean;
  isPaused: boolean;
  availableModels: ModelOption[];
  config: SessionConfig;
  files: UploadedFileItem[];
  workflow?: WorkflowPlan | null;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onPause: () => void;
  onUpload: (file: File) => void;
  onToggleThought: (messageId: string, blockId: string) => void;
  onUpdateAction?: (callId: string, status: ActionDetail["status"], content: string) => void;
  onQuickSubmit?: (text: string) => void;
  onConfigChange: (config: SessionConfig) => void;
  onPreviewFile: (fileId: string) => void;
  pendingPermissionAsk: PendingPermissionAsk | null;
  onRespondPermissionAsk: (approved: boolean) => void;
}

export function ChatArea({
  messages,
  inputValue,
  isStreaming,
  isReconnecting,
  isPaused,
  availableModels,
  config,
  files,
  workflow,
  onInputChange,
  onSubmit,
  onPause,
  onUpload,
  onToggleThought,
  onUpdateAction,
  onQuickSubmit,
  onConfigChange,
  onPreviewFile,
  pendingPermissionAsk,
  onRespondPermissionAsk,
}: ChatAreaProps) {
  const isMobile = useIsMobile();
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [workflowExpanded, setWorkflowExpanded] = useState(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!isStreaming) return;
    const interval = setInterval(() => {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 300);
    return () => clearInterval(interval);
  }, [isStreaming]);

  const completedSteps = workflow?.steps?.filter((s) => s.status === "completed").length || 0;
  const totalSteps = workflow?.steps?.length || 0;
  const hasContext = files.length > 0 || (workflow?.steps?.length || 0) > 0;

  return (
    <div className="flex-1 flex flex-col h-full relative min-w-0" style={{ background: 'hsl(var(--background))' }}>
      {messages.length === 0 ? (
        <WelcomePage
          value={inputValue}
          disabled={isStreaming && !isPaused}
          models={availableModels}
          config={config}
          files={files}
          workflow={workflow}
          onChange={onInputChange}
          onSubmit={onSubmit}
          onUpload={onUpload}
          onConfigChange={onConfigChange}
        />
      ) : (
        <>
          <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-4 sm:px-6 py-4 sm:py-6 scroll-smooth relative">
            <div
              className="absolute inset-0 pointer-events-none opacity-[0.03]"
              style={{
                backgroundImage: `linear-gradient(var(--ocean-400) 1px, transparent 1px), linear-gradient(90deg, var(--ocean-400) 1px, transparent 1px)`,
                backgroundSize: '60px 60px',
              }}
            />
            <div className={`w-full ${isMobile ? 'max-w-full' : 'max-w-[780px]'} mx-auto flex flex-col relative z-10 stagger-children`}>
              {messages.map((message) => (
                <ChatMessage key={message.id} message={message} onToggleThought={onToggleThought} onUpdateAction={onUpdateAction} onQuickSubmit={onQuickSubmit} />
              ))}
            </div>
            <div ref={bottomRef} />
          </div>

          {/* Inline context bar: files + workflow */}
          {hasContext && (
            <div className={`${isMobile ? 'w-full' : 'max-w-[780px]'} mx-auto px-4 sm:px-6`}>
              <div
                className="rounded-xl mb-0.5 overflow-hidden animate-scale-in"
                style={{
                  background: 'var(--glass-bg)',
                  border: '1px solid hsl(var(--border))',
                  backdropFilter: 'blur(8px)',
                }}
              >
                {/* File cards */}
                {files.length > 0 && (
                  <div className="flex items-start gap-3 px-4 py-3 overflow-x-auto">
                    {files.map((file) => (
                      <FileCard
                        key={file.id}
                        file={file}
                        onClick={() => onPreviewFile(file.id)}
                      />
                    ))}
                  </div>
                )}

                {/* Workflow steps */}
                {workflow?.steps && workflow.steps.length > 0 && (
                  <div style={{ borderTop: files.length > 0 ? '1px solid hsl(var(--border))' : 'none' }}>
                    <button
                      onClick={() => setWorkflowExpanded(!workflowExpanded)}
                      className="w-full flex items-center gap-2 px-3 py-2 text-[11px] transition-colors duration-200"
                      onMouseEnter={(e) => { e.currentTarget.style.background = 'hsl(var(--muted))'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                    >
                      <ListTree size={12} style={{ color: 'var(--teal-400)' }} strokeWidth={1.8} />
                      <div className="h-1 flex-1 rounded-full overflow-hidden max-w-[80px]" style={{ background: 'hsl(var(--muted))' }}>
                        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${(completedSteps / totalSteps) * 100}%`, background: 'var(--gradient-ocean-teal)' }} />
                      </div>
                      <span className="font-semibold" style={{ color: 'hsl(var(--foreground))' }}>
                        {completedSteps}/{totalSteps}
                      </span>
                      <span className="truncate max-w-[140px]" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.5 }}>
                        {workflow.steps.find(s => s.status === 'running')?.title || workflow.steps[workflow.steps.length - 1]?.title || ''}
                      </span>
                      <span className="ml-auto flex-shrink-0" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.3 }}>
                        {workflowExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      </span>
                    </button>
                    {workflowExpanded && (
                      <div className="px-3 pb-2 flex flex-col gap-0.5">
                        {workflow.steps.map((step, index) => (
                          <div key={step.key || `${index}`} className="flex items-center gap-2 py-1 px-2 rounded-md transition-colors duration-200"
                            onMouseEnter={(e) => { e.currentTarget.style.background = 'hsl(var(--muted))'; }}
                            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                          >
                            <div
                              className="w-3 h-3 rounded-full flex items-center justify-center flex-shrink-0"
                              style={{
                                background: step.status === "completed"
                                  ? 'var(--teal-50)'
                                  : step.status === "running"
                                    ? 'var(--ocean-50)'
                                    : step.status === "error"
                                      ? '#fef2f2'
                                      : 'hsl(var(--muted))',
                                color: step.status === "completed"
                                  ? 'var(--teal-500)'
                                  : step.status === "running"
                                    ? 'var(--ocean-500)'
                                    : step.status === "error"
                                      ? 'hsl(var(--destructive))'
                                      : 'hsl(var(--muted-foreground))',
                              }}
                            >
                              {step.status === "running" ? (
                                <SparkleIcon size={7} className="animate-star-spin-breathe" />
                              ) : step.status === "completed" ? (
                                <svg width="7" height="7" viewBox="0 0 24 24" fill="currentColor"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z" /></svg>
                              ) : step.status === "error" ? (
                                <svg width="7" height="7" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10" /></svg>
                              ) : (
                                <svg width="7" height="7" viewBox="0 0 24 24" fill="currentColor" opacity="0.3"><circle cx="12" cy="12" r="10" /></svg>
                              )}
                            </div>
                            <span
                              className="text-[10px] truncate"
                              style={{
                                color: step.status === "completed"
                                  ? 'var(--teal-600)'
                                  : step.status === "error"
                                    ? 'hsl(var(--destructive))'
                                    : step.status === "running"
                                      ? 'hsl(var(--foreground))'
                                      : 'hsl(var(--muted-foreground))',
                                opacity: step.status === "pending" ? 0.45 : 1,
                              }}
                            >
                              {step.title || step.label}
                            </span>
                            {step.status === "running" && (
                              <span className="ml-auto text-[8px] font-semibold animate-pulse-subtle flex-shrink-0" style={{ color: 'var(--ocean-400)', opacity: 0.5 }}>
                                执行中
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Selected file preview */}
              </div>
            </div>
          )}

          <ChatComposer
            value={inputValue}
            disabled={isStreaming && !isPaused}
            isRunning={isStreaming}
            isReconnecting={isReconnecting}
            models={availableModels}
            config={config}
            onChange={onInputChange}
            onSubmit={onSubmit}
            onPause={onPause}
            onUpload={onUpload}
            onConfigChange={onConfigChange}
            pendingPermissionAsk={pendingPermissionAsk}
            onRespondPermissionAsk={onRespondPermissionAsk}
          />
        </>
      )}
    </div>
  );
}

function SparkleIcon({ size = 12, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
      <path d="M12 0L13.5 8.5L22 6L15 12L22 18L13.5 15.5L12 24L10.5 15.5L2 18L9 12L2 6L10.5 8.5L12 0Z" fill="currentColor" />
    </svg>
  );
}

function InlinePreviewContent({ preview }: { preview: FilePreview }) {
  if (preview.preview_type === "text" || preview.preview_type === "missing" || preview.preview_type === "unsupported") {
    return <div>{preview.content || "暂无预览"}</div>;
  }
  if (preview.preview_type === "table") {
    return (
      <div className="overflow-auto rounded-lg" style={{ border: '1px solid hsl(var(--border))' }}>
        <table className="min-w-full border-collapse text-[9px]">
          <thead>
            <tr style={{ background: 'hsl(var(--muted))' }}>
              {(preview.columns || []).map((col) => (
                <th key={col} className="px-1.5 py-1 text-left font-semibold whitespace-nowrap" style={{ borderBottom: '1px solid hsl(var(--border))', color: 'hsl(var(--foreground))', opacity: 0.8 }}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(preview.rows || []).map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j} className="px-1.5 py-0.5 whitespace-nowrap" style={{ borderBottom: '1px solid hsl(var(--border))', color: 'hsl(var(--muted-foreground))', opacity: 0.6 }}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (preview.preview_type === "excel") {
    return (
      <div className="flex flex-col gap-2">
        {(preview.sheets || []).map((sheet) => (
          <div key={sheet.sheet_name}>
            <div className="text-[9px] font-bold mb-1" style={{ color: 'hsl(var(--foreground))', opacity: 0.7 }}>{sheet.sheet_name}</div>
            <div className="overflow-auto rounded-lg" style={{ border: '1px solid hsl(var(--border))' }}>
              <table className="min-w-full border-collapse text-[9px]">
                <thead>
                  <tr style={{ background: 'hsl(var(--muted))' }}>
                    {(sheet.columns || []).map((col) => (<th key={col} className="px-1.5 py-1 text-left font-semibold whitespace-nowrap" style={{ borderBottom: '1px solid hsl(var(--border))', color: 'hsl(var(--foreground))', opacity: 0.8 }}>{col}</th>))}
                  </tr>
                </thead>
                <tbody>
                  {(sheet.rows || []).map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (<td key={j} className="px-1.5 py-0.5 whitespace-nowrap" style={{ borderBottom: '1px solid hsl(var(--border))', color: 'hsl(var(--muted-foreground))', opacity: 0.6 }}>{cell}</td>))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </div>
    );
  }
  if (preview.preview_type === "document") {
    return (
      <div className="flex flex-col items-center gap-2 py-3">
        <FileText size={24} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.08 }} />
        <span style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.4 }} className="text-[10px]">{preview.content || "该文件支持在线预览"}</span>
      </div>
    );
  }
  return <div>暂无预览</div>;
}