import { createContext, useContext } from "react";
import type { ModelOption, PendingPermissionAsk, SessionConfig, UploadedFileItem, WorkflowPlan } from "@/types/app";

/**
 * ChatInteractionContext：持有聊天交互面（输入值、回调、模型/配置/文件/工作流/权限）。
 * 目的：代替 AgentPage→ChatArea 透传 ~20 个 props，并用单个 context 收敛移动端/桌面端两处调用。
 *
 * 注意：messages 不在 context 内（per-token 变化，只有 ChatArea 消费，保持 prop）。
 *       sessions / tokenUsage 也在外（Sidebar / RightPanel 变化节律不同，保持 prop）。
 */
export interface ChatInteractionValue {
  inputValue: string;
  setInputValue: (value: string) => void;
  isStreaming: boolean;
  isReconnecting: boolean;
  isPaused: boolean;
  availableModels: ModelOption[];
  config: SessionConfig;
  setConfig: (config: SessionConfig) => void;
  uploadedFiles: UploadedFileItem[];
  pendingFiles: UploadedFileItem[];
  onRemovePendingFile: (fileId: string) => void;
  workflow: WorkflowPlan | null;
  onSubmit: () => void;
  onQuickSubmit: (text: string) => void;
  onPause: () => void;
  onUpload: (file: File) => void;
  onPreviewFile: (fileId: string) => void;
  pendingPermissionAsk: PendingPermissionAsk | null;
  onRespondPermissionAsk: (approved: boolean) => void;
}

const ChatInteractionContext = createContext<ChatInteractionValue | null>(null);

export function ChatInteractionProvider({
  value,
  children,
}: {
  value: ChatInteractionValue;
  children: React.ReactNode;
}) {
  return <ChatInteractionContext.Provider value={value}>{children}</ChatInteractionContext.Provider>;
}

export function useChatInteraction(): ChatInteractionValue {
  const ctx = useContext(ChatInteractionContext);
  if (!ctx) {
    throw new Error("useChatInteraction must be used within a <ChatInteractionProvider>");
  }
  return ctx;
}
