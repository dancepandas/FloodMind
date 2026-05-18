import { useEffect, useRef } from "react";
import { ChatComposer } from "@/features/chat/components/ChatComposer";
import { ChatMessage } from "@/features/chat/components/ChatMessage";
import { WelcomePage } from "@/components/WelcomePage";
import type { ChatMessage as ChatMessageModel, ModelOption, SessionConfig, ActionDetail, PendingPermissionAsk } from "@/types/app";

interface ChatAreaProps {
  messages: ChatMessageModel[];
  inputValue: string;
  isStreaming: boolean;
  isPaused: boolean;
  availableModels: ModelOption[];
  config: SessionConfig;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onPause: () => void;
  onUpload: (file: File) => void;
  onToggleThought: (messageId: string, blockId: string) => void;
  onUpdateAction?: (callId: string, status: ActionDetail["status"], content: string) => void;
  onConfigChange: (config: SessionConfig) => void;
  pendingPermissionAsk: PendingPermissionAsk | null;
  onRespondPermissionAsk: (approved: boolean) => void;
}

export function ChatArea({
  messages,
  inputValue,
  isStreaming,
  isPaused,
  availableModels,
  config,
  onInputChange,
  onSubmit,
  onPause,
  onUpload,
  onToggleThought,
  onUpdateAction,
  onConfigChange,
  pendingPermissionAsk,
  onRespondPermissionAsk,
}: ChatAreaProps) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

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

  return (
    <div className="flex-1 flex flex-col h-full bg-background relative min-w-0">
      {messages.length === 0 ? (
        <WelcomePage onQuickAction={(text) => { onInputChange(text); onSubmit(); }} />
      ) : (
        <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-4 py-5 scroll-smooth relative">
          <div className="absolute inset-0 hydro-grid-bg opacity-30 pointer-events-none" />
          <div className="w-full max-w-[780px] mx-auto flex flex-col relative z-10">
            {messages.map((message) => (
              <ChatMessage key={message.id} message={message} onToggleThought={onToggleThought} onUpdateAction={onUpdateAction} />
            ))}
          </div>
          <div ref={bottomRef} />
        </div>
      )}

      <ChatComposer
        value={inputValue}
        disabled={isStreaming && !isPaused}
        isRunning={isStreaming}
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
    </div>
  );
}
