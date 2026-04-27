import { useEffect, useRef } from "react";
import { ChatComposer } from "@/features/chat/components/ChatComposer";
import { ChatMessage } from "@/features/chat/components/ChatMessage";
import { WelcomePage } from "@/components/WelcomePage";
import type { ChatMessage as ChatMessageModel } from "@/types/app";

interface ChatAreaProps {
  messages: ChatMessageModel[];
  inputValue: string;
  isStreaming: boolean;
  isPaused: boolean;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onPause: () => void;
  onUpload: (file: File) => void;
  onToggleThought: (messageId: string, blockId: string) => void;
}

export function ChatArea({
  messages,
  inputValue,
  isStreaming,
  isPaused,
  onInputChange,
  onSubmit,
  onPause,
  onUpload,
  onToggleThought,
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
        <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-6 py-6 scroll-smooth">
          <div className="w-full flex flex-col">
            {messages.map((message) => (
              <ChatMessage key={message.id} message={message} onToggleThought={onToggleThought} />
            ))}
          </div>
          <div ref={bottomRef} />
        </div>
      )}

      <ChatComposer
        value={inputValue}
        disabled={isStreaming && !isPaused}
        isRunning={isStreaming}
        onChange={onInputChange}
        onSubmit={onSubmit}
        onPause={onPause}
        onUpload={onUpload}
      />
    </div>
  );
}
