import { ChatComposer } from "@/features/chat/components/ChatComposer";
import { ChatMessage } from "@/features/chat/components/ChatMessage";
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
  return (
    <div className="flex-1 flex flex-col h-full bg-[linear-gradient(180deg,rgba(255,255,255,0.58)_0%,rgba(239,246,255,0.72)_100%)] relative min-w-0">
      <div className="flex-1 overflow-y-auto px-6 py-6 scroll-smooth">
        <div className="w-full flex flex-col">
          {messages.map((message) => (
            <ChatMessage key={message.id} message={message} onToggleThought={onToggleThought} />
          ))}
        </div>
      </div>

      <ChatComposer
        value={inputValue}
        disabled={isPaused}
        isRunning={isStreaming}
        onChange={onInputChange}
        onSubmit={onSubmit}
        onPause={onPause}
        onUpload={onUpload}
      />
    </div>
  );
}
