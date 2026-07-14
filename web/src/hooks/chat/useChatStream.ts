import { useCallback, useEffect, useRef, useState } from "react";
import {
  createChatRequest,
  fetchSession,
  fetchSessionFiles,
  fetchSessionStatus,
  initAgent,
  pauseSession,
  resumeStreamRequest,
  saveSession,
  uploadFile,
} from "@/api/agent";
import { buildApiUrl } from "@/api/client";
import {
  appendThoughtBlock,
  attachArtifact,
  createAssistantMessage,
  createUserMessage,
  createSystemMessage,
  fromServerMessage,
  setAssistantFinalContent,
} from "@/features/chat/lib/message-blocks";
import { MAX_SSE_RETRIES, consumeSseStream, resumeStreamWithBackoff, sleep, type SseStreamHandlers } from "@/features/chat/lib/sse-reader";
import { normalizeArtifact } from "@/features/chat/lib/session-utils";
import { createLogger } from "@/lib/logger";
import { uuid } from "@/lib/utils";
import type {
  ChatMessage,
  SessionConfig,
  SessionRuntimeState,
  ToolActivity,
  TokenUsage,
  UploadedFileItem,
  WorkflowPlan,
} from "@/types/app";
import type { SetMessages } from "@/hooks/chat/usePermission";

const log = createLogger("ChatStream");

const SLASH_COMMANDS: Record<string, string> = {
  "/help": "显示所有可用命令",
  "/logs": "下载应用日志文件（zip）",
  "/files": "下载当前会话输出文件（zip）",
  "/clear": "清除当前会话记忆",
  "/status": "查看当前会话状态",
  "/config": "查看当前模型配置",
};

export interface UseChatStreamArgs {
  sessionId: string;
  config: SessionConfig;
  refreshSessionIndex: () => Promise<void>;
}

/**
 * 聊天流核心：拥有消息、流式/重连/压缩态、工作流、工具活动、token 用量、运行态、上传文件。
 * 负责 session-init（恢复历史 + 续流）、visibilitychange 重连、提交与断线续传。
 * 交互的"写消息"入口（setMessages）对外暴露，供 usePermission 等派生 hook 复用。
 */
export function useChatStream({ sessionId, config, refreshSessionIndex }: UseChatStreamArgs) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileItem[]>([]);
  const [pendingFiles, setPendingFiles] = useState<UploadedFileItem[]>([]);
  const [toolActivities, setToolActivities] = useState<ToolActivity[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowPlan | null>(null);
  const [sessionTokenUsage, setSessionTokenUsage] = useState<TokenUsage>({
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
  });
  const [tokenHistory, setTokenHistory] = useState<TokenUsage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isContextCompressing, setIsContextCompressing] = useState(false);
  const [runtimeState, setRuntimeState] = useState<SessionRuntimeState>({ isPaused: false });
  const [isReconnecting, setIsReconnecting] = useState(false);

  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  // 用户主动停止标记：阻止 handleSubmit 在 reader.cancel() 后误触发自动重连
  const stopRequestedRef = useRef(false);
  // 排队 in-flight 护栏：防止快速双回车重复排队
  const isQueuingRef = useRef(false);
  const configRef = useRef(config);
  configRef.current = config;
  const initializedSessionRef = useRef<string | null>(null);
  const initPromiseRef = useRef<Promise<void> | null>(null);
  const wasStreamingRef = useRef(false);
  const postSubmitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /* ─── token 用量累计 ─── */
  const recordTokenUsage = useCallback((usage: TokenUsage, messageId?: string) => {
    if (usage.total_tokens <= 0 && usage.prompt_tokens <= 0 && usage.completion_tokens <= 0) return;
    if (messageId) {
      setMessages((prev) => prev.map((msg) => (msg.id === messageId ? { ...msg, tokenUsage: usage } : msg)));
    }
    setSessionTokenUsage((prev) => ({
      prompt_tokens: prev.prompt_tokens + usage.prompt_tokens,
      completion_tokens: prev.completion_tokens + usage.completion_tokens,
      total_tokens: prev.total_tokens + usage.total_tokens,
    }));
    setTokenHistory((prev) => [...prev.slice(-9), usage]);
  }, []);

  /* ─── 工具活动（侧栏/调试用） ─── */
  const pushToolActivity = useCallback((toolName: string, content: string, status: ToolActivity["status"]) => {
    setToolActivities((prev) => {
      if (status === "done" || status === "error") {
        const idx = prev.findIndex((t) => t.toolName === toolName && t.status === "running");
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = { ...updated[idx], status, content: content || updated[idx].content };
          log.debug(`pushToolActivity: updated [${toolName}] running→${status} (idx=${idx})`);
          return updated;
        }
        log.debug(`pushToolActivity: no running entry for [${toolName}], creating new ${status}`);
      } else {
        log.debug(`pushToolActivity: new [${toolName}] status=${status}`);
      }
      return [
        {
          id: uuid(),
          toolName,
          content,
          status,
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
        },
        ...prev,
      ].slice(0, 24);
    });
  }, []);

  const refreshFiles = useCallback(async (targetSessionId: string) => {
    const files = await fetchSessionFiles(targetSessionId);
    setUploadedFiles(files);
  }, []);

  /** 构造针对某条消息的 updateAssistant 闭包（SSE handlers 用）。 */
  const updateFor = useCallback(
    (messageId: string) => (updater: (message: ChatMessage) => ChatMessage) => {
      setMessages((prev) => prev.map((message) => (message.id === messageId ? updater(message) : message)));
    },
    []
  );

  /** 为一次流构造完整 handlers（updateAssistant 绑定到指定消息）。 */
  const buildHandlers = useCallback(
    (messageId: string): SseStreamHandlers => ({
      updateAssistant: updateFor(messageId),
      pushToolActivity,
      setWorkflow,
      setIsContextCompressing,
      setTokenUsage: (usage: TokenUsage) => recordTokenUsage(usage, messageId),
    }),
    [pushToolActivity, recordTokenUsage, updateFor]
  );

  /* ─── session-init：恢复历史消息 + 续接未完成流 ─── */
  useEffect(() => {
    if (initializedSessionRef.current === sessionId) return;
    if (initPromiseRef.current) return;

    let active = true;
    log.info("Session init effect: sessionId=", sessionId);

    const run = async () => {
      try {
        setToolActivities([]);
        setWorkflow(null);

        await initAgent(sessionId, configRef.current);
        if (!active) return;
        initializedSessionRef.current = sessionId;

        await refreshSessionIndex();
        if (!active) return;

        let loadedMessages: ChatMessage[] = [];
        try {
          const detail = await fetchSession(sessionId);
          if (!active) return;
          const restoredMessages = (detail.messages || []).map(fromServerMessage);
          log.info("loadSession → restored", restoredMessages.length, "messages");
          const restoredArtifacts = (detail.artifacts || [])
            .map((artifact) => normalizeArtifact(artifact as Record<string, unknown>))
            .filter((artifact): artifact is NonNullable<typeof artifact> => artifact !== null);
          if (restoredArtifacts.length > 0) {
            log.info("loadSession →", restoredArtifacts.length, "artifacts to attach");
            const lastAssistantIndex = [...restoredMessages]
              .map((message, index) => ({ message, index }))
              .reverse()
              .find(({ message }) => message.role === "FloodMind")?.index;
            if (lastAssistantIndex !== undefined) {
              let targetMessage = restoredMessages[lastAssistantIndex];
              restoredArtifacts.forEach((artifact) => {
                targetMessage = attachArtifact(targetMessage, artifact);
              });
              restoredMessages[lastAssistantIndex] = targetMessage;
            }
          }
          loadedMessages = restoredMessages;
          setMessages(restoredMessages);
          if (detail.in_progress?.workflow) {
            setWorkflow(detail.in_progress.workflow);
          }
        } catch (err) {
          log.warn("loadSession failed, resetting state", err);
          setMessages([]);
          setToolActivities([]);
          setWorkflow(null);
        }
        await refreshFiles(sessionId);
        if (!active) return;
        const status = await fetchSessionStatus(sessionId);
        if (!active) return;
        setRuntimeState({ isPaused: !!status.session_state?.is_paused });
        if (status.in_progress?.message_id) {
          const inProgressId = status.in_progress.message_id;
          const isStillStreaming = status.in_progress.is_streaming;

          if (isStillStreaming) {
            log.info("App init → resuming in_progress stream", inProgressId);
            const assistantMessage = createAssistantMessage(inProgressId);
            setMessages((prev) => [...prev, assistantMessage]);
            setIsStreaming(true);

            try {
              const response = await resumeStreamRequest(sessionId);
              if (response.ok && response.body) {
                const reader = response.body.getReader();
                readerRef.current = reader;
                await consumeSseStream(reader, buildHandlers(inProgressId));
              }
            } catch (err) {
              log.warn("Resume stream failed, falling back to snapshot", err);
            } finally {
              setIsStreaming(false);
              readerRef.current = null;
            }
          } else {
            const alreadyExists = loadedMessages.some(
              (m) => m.id === inProgressId || (m.role === "FloodMind" && m.content === (status.in_progress?.content || "")),
            );
            if (alreadyExists) {
              log.info("App init → in_progress already in restored messages, skipping", inProgressId);
            } else {
              log.info("App init → restoring completed in_progress message", inProgressId);
              const restored = createAssistantMessage(inProgressId);
              let hydrated = restored;
              if (status.in_progress.reasoning) hydrated = appendThoughtBlock(hydrated, status.in_progress.reasoning, false);
              if (status.in_progress.content) hydrated = setAssistantFinalContent(hydrated, status.in_progress.content);
              (status.in_progress.artifacts || []).forEach((artifact) => {
                hydrated = attachArtifact(hydrated, artifact);
              });
              setMessages((prev) => [...prev, hydrated]);
            }
          }
          if (status.in_progress.workflow) setWorkflow(status.in_progress.workflow);
        }
      } finally {
        initPromiseRef.current = null;
      }
    };

    initPromiseRef.current = run().catch((err) => {
      log.error("Session init effect failed", err);
    });

    return () => {
      active = false;
      readerRef.current?.cancel().catch(() => undefined);
      initPromiseRef.current = null;
      if (postSubmitTimerRef.current !== null) {
        clearTimeout(postSubmitTimerRef.current);
        postSubmitTimerRef.current = null;
      }
    };
    // config 经 configRef 读取（避免切模型触发重建）；refreshSessionIndex / buildHandlers 稳定。
  }, [sessionId, refreshSessionIndex, refreshFiles, buildHandlers]);

  /* ─── Auto-reconnect：页面重新可见时续接丢失的流 ─── */
  useEffect(() => {
    const handleVisibility = async () => {
      if (document.visibilityState !== "visible") return;
      if (!wasStreamingRef.current) return;
      if (readerRef.current) return;
      // 用户主动停止时不自动重连（避免与 handlePauseResume 的 cancel 竞态）
      if (stopRequestedRef.current) return;

      log.info("visibilitychange: page visible, resuming lost stream");
      setIsReconnecting(true);
      let retries = 0;
      while (retries < MAX_SSE_RETRIES) {
        try {
          const status = await fetchSessionStatus(sessionId);
          if (status.in_progress?.message_id && status.in_progress.is_streaming) {
            const inProgressId = status.in_progress.message_id;
            log.info("visibilitychange: resuming stream", inProgressId);
            const assistantMessage = createAssistantMessage(inProgressId);
            setMessages((prev) => [...prev, assistantMessage]);
            setIsStreaming(true);

            const response = await resumeStreamRequest(sessionId);
            if (response.ok && response.body) {
              const reader = response.body.getReader();
              readerRef.current = reader;
              await consumeSseStream(reader, buildHandlers(inProgressId));
            }
          }
          break;
        } catch (err) {
          retries++;
          log.warn(`visibilitychange: resume attempt ${retries}/${MAX_SSE_RETRIES} failed`, err);
          if (retries >= MAX_SSE_RETRIES) break;
          await sleep(Math.min(1000 * Math.pow(2, retries), 30000));
        }
      }
      setIsReconnecting(false);
      wasStreamingRef.current = false;
    };

    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, [sessionId, buildHandlers]);

  /* ─── 斜杠命令 ─── */
  const handleSlashCommand = useCallback(
    (cmd: string): boolean => {
      const parts = cmd.split(/\s+/);
      const command = parts[0].toLowerCase();
      if (!SLASH_COMMANDS[command]) return false;
      setInputValue("");

      switch (command) {
        case "/help": {
          const lines = Object.entries(SLASH_COMMANDS).map(([k, v]) => `  ${k.padEnd(10)} ${v}`);
          setMessages((prev) => [...prev, createSystemMessage(`可用命令：\n${lines.join("\n")}`)]);
          break;
        }
        case "/logs": {
          setMessages((prev) => [...prev, createSystemMessage("正在下载日志文件...")]);
          window.open(buildApiUrl("/api/logs"), "_blank");
          break;
        }
        case "/files": {
          setMessages((prev) => [...prev, createSystemMessage("正在下载会话输出文件...")]);
          window.open(buildApiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/outputs/download`), "_blank");
          break;
        }
        case "/clear": {
          fetch(buildApiUrl("/api/clear"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId }),
          })
            .then(() => setMessages((prev) => [...prev, createSystemMessage("会话记忆已清除")]))
            .catch((err) => setMessages((prev) => [...prev, createSystemMessage(`清除失败: ${err.message}`)]));
          break;
        }
        case "/status": {
          fetchSessionStatus(sessionId)
            .then((res) => setMessages((prev) => [...prev, createSystemMessage(`会话状态：\n${JSON.stringify(res, null, 2)}`)]))
            .catch((err) => setMessages((prev) => [...prev, createSystemMessage(`获取状态失败: ${err.message}`)]));
          break;
        }
        case "/config": {
          fetch(buildApiUrl("/api/config"))
            .then((r) => r.json())
            .then((data) => setMessages((prev) => [...prev, createSystemMessage(`模型配置：\n${JSON.stringify(data, null, 2)}`)]))
            .catch((err) => setMessages((prev) => [...prev, createSystemMessage(`获取配置失败: ${err.message}`)]));
          break;
        }
      }
      return true;
    },
    [sessionId]
  );

  const handleSubmit = useCallback(async () => {
    const content = inputValue.trim();
    if (!content) return;
    if (content.startsWith("/") && handleSlashCommand(content)) return;

    // agent 运行中发送 = 排队：只把用户消息上屏，后端 append 到 memory，
    // 运行中的流会在下一次 LLM 调用带上（不另开流、不打扰运行中的 reader）。
    if (isStreaming) {
      if (isQueuingRef.current) return; // 排队请求在途，避免快速双发重复排队
      log.info("handleSubmit: queuing message while streaming", content.slice(0, 80));
      const userMessage = createUserMessage(content, pendingFiles);
      setMessages((prev) => [...prev, userMessage]);
      setInputValue("");
      setPendingFiles([]);
      isQueuingRef.current = true;
      try {
        const response = await createChatRequest(sessionId, content, pendingFiles.map((f) => f.id), uuid());
        // 202 = 排队成功（非 SSE，无 body stream）；其他非 ok 视为错误
        if (!response.ok && response.status !== 202) {
          let detail = `排队失败: ${response.status}`;
          try {
            const body = await response.json();
            if (body.error) detail = body.error;
          } catch {
            /* 响应体非 JSON */
          }
          setMessages((prev) => [...prev, createSystemMessage(detail)]);
        }
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        setMessages((prev) => [...prev, createSystemMessage(`发送失败: ${errMsg}`)]);
      } finally {
        isQueuingRef.current = false;
      }
      return;
    }

    log.info("handleSubmit: sending message", content.slice(0, 80));
    const userMessage = createUserMessage(content, pendingFiles);
    const assistantMessage = createAssistantMessage();
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setInputValue("");
    setPendingFiles([]);
    setIsStreaming(true);
    wasStreamingRef.current = true;
    let eventCount = 0;

    const logArtifactEvent = (data: Record<string, unknown>) => {
      if (data.type === "image_generated" || data.type === "file_generated") {
        log.info(`[SSE] 收到artifact事件: type=${data.type}, filename=${data.filename}, image_url=${data.image_url || ""}, download_url=${data.download_url || ""}, isStreaming=${true}`);
      }
      if (data.type === "stream_end") {
        log.info(`[SSE] 收到stream_end事件, 此时isStreaming=true`);
      }
    };

    try {
      const response = await createChatRequest(sessionId, content, pendingFiles.map((file) => file.id), assistantMessage.id);
      if (!response.ok) {
        let detail = `Chat request failed: ${response.status}`;
        try {
          const body = await response.json();
          if (body.error) detail = body.error;
        } catch {
          /* 响应体非 JSON（网关错误页等），沿用默认 detail */
          log.debug(`Failed to parse error response body as JSON for HTTP ${response.status}`);
        }
        throw new Error(detail);
      }
      if (!response.body) {
        throw new Error(`Chat request failed: ${response.status}`);
      }

      const reader = response.body.getReader();
      readerRef.current = reader;
      log.info("SSE stream reading started");
      eventCount = await consumeSseStream(reader, buildHandlers(assistantMessage.id), { onEvent: logArtifactEvent });
      log.info(`SSE stream ended, total events=${eventCount}`);
    } catch (error) {
      // 用户主动停止（reader.cancel）导致的异常：不重连、不报错
      if (stopRequestedRef.current) {
        log.info("handleSubmit: stream cancelled by user stop, skipping reconnect");
      } else {
        const errMsg = error instanceof Error ? error.message : String(error);
        const isClientError = errMsg.includes("400") || errMsg.includes("不支持的") || errMsg.includes("不支持");
        // consumeSseStream 在异常时将 eventCount 附加到 Error 上，供断线重续使用正确的 after_index
        const resumeIndex = (error instanceof Error ? (error as Error & { eventCount?: number }).eventCount : undefined) ?? eventCount;
        log.error("handleSubmit: stream error", error);
        if (isClientError) {
          setMessages((prev) =>
            prev.map((message) => (message.id === assistantMessage.id ? setAssistantFinalContent(message, errMsg) : message)),
          );
        } else {
          const resumed = await resumeStreamWithBackoff(sessionId, resumeIndex, buildHandlers(assistantMessage.id), {
            onReader: (r) => {
              readerRef.current = r;
            },
            onEvent: logArtifactEvent,
          });
          if (!resumed) {
            log.warn("handleSubmit: all retries exhausted, showing error");
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessage.id ? setAssistantFinalContent(message, "抱歉，连接中断，已尝试自动重连但未恢复。") : message,
              ),
            );
          }
        }
      }
    } finally {
      stopRequestedRef.current = false;
      setIsStreaming(false);
      wasStreamingRef.current = false;
      readerRef.current = null;
      log.info("handleSubmit: saving session");
      await saveSession(sessionId);
      await refreshSessionIndex();
      if (postSubmitTimerRef.current !== null) clearTimeout(postSubmitTimerRef.current);
      postSubmitTimerRef.current = setTimeout(() => {
        postSubmitTimerRef.current = null;
        refreshSessionIndex();
      }, 5000);
      log.info("handleSubmit: complete");
    }
  }, [buildHandlers, handleSlashCommand, inputValue, isStreaming, refreshSessionIndex, sessionId, uploadedFiles]);

  const handleQuickSubmit = useCallback(async (text: string) => {
    const content = text.trim();
    if (!content || isStreaming) return;
    log.info("handleQuickSubmit:", content.slice(0, 80));
    const userMessage = createUserMessage(content);
    const assistantMessage = createAssistantMessage();
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsStreaming(true);
    wasStreamingRef.current = true;
    let eventCount = 0;

    try {
      const response = await createChatRequest(sessionId, content, [], assistantMessage.id);
      if (!response.ok || !response.body) {
        throw new Error(`Chat request failed: ${response.status}`);
      }
      const reader = response.body.getReader();
      readerRef.current = reader;
      eventCount = await consumeSseStream(reader, buildHandlers(assistantMessage.id));
    } catch (error) {
      const resumeIndex = (error instanceof Error ? (error as Error & { eventCount?: number }).eventCount : undefined) ?? eventCount;
      log.error("handleQuickSubmit: stream error", error);
      const resumed = await resumeStreamWithBackoff(sessionId, resumeIndex, buildHandlers(assistantMessage.id), {
        onReader: (r) => {
          readerRef.current = r;
        },
      });
      if (!resumed) {
        setMessages((prev) =>
          prev.map((message) => (message.id === assistantMessage.id ? setAssistantFinalContent(message, "抱歉，连接中断。") : message)),
        );
      }
    } finally {
      setIsStreaming(false);
      wasStreamingRef.current = false;
      readerRef.current = null;
      await saveSession(sessionId);
      await refreshSessionIndex();
    }
  }, [buildHandlers, isStreaming, pendingFiles, refreshSessionIndex, sessionId]);

  const handleUpload = useCallback(
    async (file: File) => {
      const item = await uploadFile(sessionId, file);
      setPendingFiles((prev) => [...prev, item]);
      await refreshFiles(sessionId);
    },
    [refreshFiles, sessionId]
  );

  const removePendingFile = useCallback((fileId: string) => {
    setPendingFiles((prev) => prev.filter((f) => f.id !== fileId));
  }, []);

  const toggleThought = useCallback((messageId: string, blockId: string) => {
    setMessages((prev) =>
      prev.map((message) => {
        if (message.id !== messageId) return message;
        return {
          ...message,
          blocks: message.blocks.map((block) => (block.id === blockId ? { ...block, isCollapsed: !block.isCollapsed } : block)),
        };
      }),
    );
  }, []);

  const handlePauseResume = useCallback(async () => {
    // 统一为“停止”：暂停 = 中止当前流（abort），未完成轮丢弃。
    // “恢复”= 用户再次发送（handleSubmit 从 memory 起步），无需单独 resume。
    log.info("handlePauseResume: stopping (abort current stream)");
    stopRequestedRef.current = true;
    await pauseSession(sessionId);
    readerRef.current?.cancel().catch(() => undefined);
    readerRef.current = null;
    setIsStreaming(false);
    setRuntimeState({ isPaused: false });
  }, [sessionId]);

  /** 新建会话时清空所有会话级瞬时态（消息由调用方随后切换 sessionId 触发 init 重建）。 */
  const resetTransientState = useCallback(() => {
    setMessages([]);
    setUploadedFiles([]);
    setPendingFiles([]);
    setToolActivities([]);
    setWorkflow(null);
    setIsContextCompressing(false);
    setRuntimeState({ isPaused: false });
  }, []);

  return {
    messages,
    setMessages,
    uploadedFiles,
    pendingFiles,
    removePendingFile,
    toolActivities,
    workflow,
    sessionTokenUsage,
    tokenHistory,
    runtimeState,
    inputValue,
    setInputValue,
    isStreaming,
    isContextCompressing,
    isReconnecting,
    handleSubmit,
    handleQuickSubmit,
    handleUpload,
    toggleThought,
    handlePauseResume,
    resetTransientState,
  };
}
