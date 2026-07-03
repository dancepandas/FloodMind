import { useEffect, useRef, useCallback } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChatComposer } from "@/features/chat/components/ChatComposer";
import { ChatMessage } from "@/features/chat/components/ChatMessage";
import { PermissionBanner } from "@/features/chat/components/PermissionBanner";
import { ReconnectBanner } from "@/features/chat/components/ReconnectBanner";
import WelcomePage from "@/components/WelcomePage";
import { useIsMobile } from "@/hooks/use-mobile";
import { useChatInteraction } from "@/features/chat/ChatInteractionContext";
import type { ChatMessage as ChatMessageModel } from "@/types/app";

interface ChatAreaProps {
  messages: ChatMessageModel[];
  onToggleThought: (messageId: string, blockId: string) => void;
}

export function ChatArea({ messages, onToggleThought }: ChatAreaProps) {
  const isMobile = useIsMobile();
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 从 ChatInteractionContext 读取交互面（代替原先 AgentPage 透传的 ~18 个 props）。
  const {
    inputValue,
    setInputValue,
    isStreaming,
    isReconnecting,
    availableModels,
    config,
    setConfig,
    uploadedFiles,
    pendingFiles,
    onRemovePendingFile,
    workflow,
    onSubmit,
    onPause,
    onUpload,
    onQuickSubmit,
    onPreviewFile,
    pendingPermissionAsk,
    onRespondPermissionAsk,
  } = useChatInteraction();

  // 阶段1.3 虚拟化：长对话只渲染可见消息，避免 100+ 消息时 DOM 节点爆炸。
  const rowVirtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => 220,
    overscan: 6,
  });

  const scrollToBottom = useCallback(() => {
    if (messages.length === 0) return;
    rowVirtualizer.scrollToIndex(messages.length - 1, { align: "end" });
  }, [messages.length, rowVirtualizer]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  useEffect(() => {
    if (!isStreaming) return;
    const interval = setInterval(() => {
      scrollToBottom();
    }, 300);
    return () => clearInterval(interval);
  }, [isStreaming, scrollToBottom]);

  return (
    <div className="flex-1 flex flex-col h-full relative min-w-0" style={{ background: 'var(--bg)' }}>
      {messages.length === 0 ? (
        <WelcomePage
          value={inputValue}
          disabled={false}
          models={availableModels}
          config={config}
          files={uploadedFiles}
          workflow={workflow}
          onChange={setInputValue}
          onSubmit={onSubmit}
          onUpload={onUpload}
          onConfigChange={setConfig}
        />
      ) : (
        <>
          {/* Banner 条：移出滚动区，常驻可见（PermissionBanner 不被滚走，体验更稳）。 */}
          {(isReconnecting || pendingPermissionAsk) && (
            <div className="px-7 pt-4 mx-auto w-full max-w-[760px] flex flex-col gap-2">
              {isReconnecting && <ReconnectBanner />}
              {pendingPermissionAsk && (
                <PermissionBanner ask={pendingPermissionAsk} onRespond={onRespondPermissionAsk} />
              )}
            </div>
          )}

          <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-7 py-7 scroll-smooth relative">
            <div
              className="absolute inset-0 pointer-events-none opacity-[0.02]"
              style={{
                backgroundImage: `linear-gradient(var(--border) 1px, transparent 1px), linear-gradient(90deg, var(--border) 1px, transparent 1px)`,
                backgroundSize: '64px 64px',
              }}
            />
            <div className={`w-full ${isMobile ? 'max-w-full' : 'max-w-[760px]'} mx-auto relative z-10`}>
              {/* 虚拟列表：外层占位撑起总高度，内层绝对定位渲染可见项 */}
              <div
                style={{
                  height: `${rowVirtualizer.getTotalSize()}px`,
                  position: 'relative',
                  width: '100%',
                }}
              >
                {rowVirtualizer.getVirtualItems().map((virtualItem) => {
                  const message = messages[virtualItem.index];
                  if (!message) return null;
                  return (
                    <div
                      key={message.id}
                      data-index={virtualItem.index}
                      ref={rowVirtualizer.measureElement}
                      style={{
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        width: '100%',
                        transform: `translateY(${virtualItem.start}px)`,
                      }}
                    >
                      <ChatMessage
                        message={message}
                        onToggleThought={onToggleThought}
                        onQuickSubmit={onQuickSubmit}
                        onPreviewFile={onPreviewFile}
                      />
                    </div>
                  );
                })}
              </div>
              <div ref={bottomRef} />
            </div>
          </div>

          <ChatComposer
            value={inputValue}
            disabled={false}
            isRunning={isStreaming}
            isReconnecting={isReconnecting}
            models={availableModels}
            config={config}
            onChange={setInputValue}
            onSubmit={onSubmit}
            onPause={onPause}
            onUpload={onUpload}
            pendingFiles={pendingFiles}
            onRemovePendingFile={onRemovePendingFile}
            onConfigChange={setConfig}
          />
        </>
      )}
    </div>
  );
}
