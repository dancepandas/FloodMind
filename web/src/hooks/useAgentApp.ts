import { useCallback } from "react";
import { deleteSession } from "@/api/agent";
import { useArtifacts } from "@/hooks/chat/useArtifacts";
import { useChatStream } from "@/hooks/chat/useChatStream";
import { useModels } from "@/hooks/chat/useModels";
import { usePermission } from "@/hooks/chat/usePermission";
import { useSession } from "@/hooks/chat/useSession";
import { generateSessionId } from "@/features/chat/lib/session-utils";
import { createLogger } from "@/lib/logger";

const log = createLogger("App");

/**
 * 聚合器：把按关注点拆分的领域 hook 组合为统一的 App API，并编排跨切片操作
 * （新建/删除/切换会话需同时重置会话身份、聊天流瞬时态与文件预览）。
 * 各领域 hook（useSession/useModels/useChatStream/useArtifacts/usePermission）各自独立可测。
 */
export function useAgentApp() {
  const { sessionId, setSessionId, sessions, refreshSessionIndex, loadSession: switchSession } = useSession();
  const { availableModels, config, setConfig } = useModels(sessionId);
  const chat = useChatStream({ sessionId, config, refreshSessionIndex });
  const { allArtifacts, selectedPreview, handlePreviewFile, closePreview } = useArtifacts(chat.messages, sessionId);
  const { pendingPermissionAsk, handleRespondPermissionAsk } = usePermission(chat.messages, chat.setMessages);

  const handleNewSession = useCallback(() => {
    const nextSessionId = generateSessionId();
    log.info("handleNewSession", nextSessionId);
    chat.resetTransientState();
    closePreview();
    setSessionId(nextSessionId);
  }, [chat, closePreview, setSessionId]);

  const handleDeleteSession = useCallback(
    async (targetSessionId: string) => {
      log.info("handleDeleteSession", targetSessionId);
      await deleteSession(targetSessionId);
      await refreshSessionIndex();
      if (targetSessionId === sessionId) {
        handleNewSession();
      }
    },
    [handleNewSession, refreshSessionIndex, sessionId]
  );

  // 切换会话时清空预览（原 session-init effect 内 setSelectedPreview(null) 的等价行为，
  // 放在聚合器避免 useArtifacts 内的 effect 同步 setState）。
  const loadSession = useCallback(
    (targetSessionId: string) => {
      closePreview();
      switchSession(targetSessionId);
    },
    [closePreview, switchSession]
  );

  return {
    sessionId,
    sessions,
    messages: chat.messages,
    uploadedFiles: chat.uploadedFiles,
    pendingFiles: chat.pendingFiles,
    removePendingFile: chat.removePendingFile,
    toolActivities: chat.toolActivities,
    workflow: chat.workflow,
    sessionTokenUsage: chat.sessionTokenUsage,
    tokenHistory: chat.tokenHistory,
    allArtifacts,
    selectedPreview,
    runtimeState: chat.runtimeState,
    inputValue: chat.inputValue,
    isStreaming: chat.isStreaming,
    isContextCompressing: chat.isContextCompressing,
    isReconnecting: chat.isReconnecting,
    availableModels,
    setInputValue: chat.setInputValue,
    handleSubmit: chat.handleSubmit,
    handleUpload: chat.handleUpload,
    handlePreviewFile,
    handlePauseResume: chat.handlePauseResume,
    handleNewSession,
    handleDeleteSession,
    handleQuickSubmit: chat.handleQuickSubmit,
    loadSession,
    toggleThought: chat.toggleThought,
    pendingPermissionAsk,
    handleRespondPermissionAsk,
    closePreview,
    config,
    setConfig,
  };
}
