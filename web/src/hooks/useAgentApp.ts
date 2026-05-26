import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createChatRequest,
  deleteSession,
  downloadLogsZip,
  downloadSessionOutputs,
  fetchFilePreview,
  fetchSession,
  fetchSessionFiles,
  fetchSessions,
  fetchSessionStatus,
  fetchModels,
  initAgent,
  pauseSession,
  respondPermissionAsk as respondPermissionAskApi,
  resumeStreamRequest,
  resumeSession,
  saveSession,
  updateSessionConfig,
  uploadFile,
} from "@/api/agent";
import { buildApiUrl } from "@/api/client";
import {
  appendThoughtBlock,
  attachArtifact,
  createAssistantMessage,
  createUserMessage,
  createSystemMessage,
  finalizeThoughtBlocks,
  fromServerMessage,
  setAssistantFinalContent,
  updateActionBlockStatus,
} from "@/features/chat/lib/message-blocks";
import { createLogger } from "@/lib/logger";
import { uuid } from "@/lib/utils";
import { applyStreamEvent } from "@/features/chat/lib/stream-events";
import type {
  ChatMessage,
  FilePreview,
  GeneratedArtifact,
  ModelOption,
  PendingPermissionAsk,
  SessionConfig,
  SessionRuntimeState,
  SessionSummary,
  ToolActivity,
  UploadedFileItem,
  WorkflowPlan,
  ActionDetail,
} from "@/types/app";

const log = createLogger("App");

const STORAGE_KEY = "floodmind_react_session_id";
const MAX_RETRIES = 10;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function generateSessionId() {
  return `session-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function normalizeArtifact(raw: Record<string, unknown>): GeneratedArtifact | null {
  const type = raw.type;
  const filename = raw.filename;
  if ((type !== "file_generated" && type !== "image_generated") || typeof filename !== "string") {
    return null;
  }

  return {
    type,
    filename,
    filepath: typeof raw.filepath === "string" ? raw.filepath : undefined,
    size: typeof raw.size === "number" ? raw.size : undefined,
    download_url: typeof raw.download_url === "string" ? raw.download_url : undefined,
    image_url: typeof raw.image_url === "string" ? raw.image_url : undefined,
    image_data: typeof raw.image_data === "string" ? raw.image_data : undefined,
  };
}

export function useAgentApp() {
  const [sessionId, setSessionId] = useState(() => {
    const stored = localStorage.getItem(STORAGE_KEY) || generateSessionId();
    log.info("初始化 sessionId=", stored);
    return stored;
  });
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileItem[]>([]);
  const [toolActivities, setToolActivities] = useState<ToolActivity[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowPlan | null>(null);
  const [selectedPreview, setSelectedPreview] = useState<FilePreview | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [config, setConfig] = useState<SessionConfig>({ model_key: "deepseek_v4_flash", enable_search: true, enable_rag: true, enable_reasoning: true });
  const [runtimeState, setRuntimeState] = useState<SessionRuntimeState>({ isPaused: false });
  const [isReconnecting, setIsReconnecting] = useState(false);
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const configRef = useRef(config);
  configRef.current = config;
  const initializedSessionRef = useRef<string | null>(null);
  const initPromiseRef = useRef<Promise<void> | null>(null);
  const wasStreamingRef = useRef(false);

  const refreshSessionIndex = useCallback(async () => {
    const items = await fetchSessions();
    setSessions(items);
  }, []);

  const refreshFiles = useCallback(async (targetSessionId: string) => {
    const files = await fetchSessionFiles(targetSessionId);
    setUploadedFiles(files);
  }, []);

  const loadSession = useCallback(async (targetSessionId: string) => {
    if (targetSessionId === sessionId) return;
    log.info("loadSession: switching to", targetSessionId);
    setSessionId(targetSessionId);
    localStorage.setItem(STORAGE_KEY, targetSessionId);
  }, [sessionId]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, sessionId);
  }, [sessionId]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const modelsRes = await fetchModels();
        if (!active) return;
        const models = modelsRes.models || [];
        setAvailableModels(models);
        const defaultModel = models.find((m) => m.is_default) || models[0];
        if (defaultModel) {
          setConfig((prev) =>
            prev.model_key === defaultModel.key ? prev : { ...prev, model_key: defaultModel.key }
          );
        }
      } catch (err) {
        log.warn("fetchModels failed, using defaults", err);
      }
    })();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (initializedSessionRef.current === sessionId) return;
    if (initPromiseRef.current) return;

    let active = true;
    log.info("Session init effect: sessionId=", sessionId);

    const run = async () => {
      try {
        setSelectedPreview(null);
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
            .filter((artifact): artifact is GeneratedArtifact => artifact !== null);
          if (restoredArtifacts.length > 0) {
            log.info("loadSession →", restoredArtifacts.length, "artifacts to attach");
            const lastAssistantIndex = [...restoredMessages].map((message, index) => ({ message, index })).reverse().find(({ message }) => message.role === "FloodMind")?.index;
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
                const decoder = new TextDecoder();
                let buffer = "";

                const updateAssistant = (updater: (message: ChatMessage) => ChatMessage) => {
                  setMessages((prev) => prev.map((message) => (message.id === inProgressId ? updater(message) : message)));
                };

                while (true) {
                  const { done, value } = await reader.read();
                  if (done) break;
                  buffer += decoder.decode(value, { stream: true });
                  const lines = buffer.split("\n");
                  buffer = lines.pop() || "";
                  for (const line of lines) {
                    const trimmed = line.trim();
                    if (!trimmed) continue;
                    try {
                      const data = JSON.parse(trimmed) as Record<string, any>;
                      applyStreamEvent(data, {
                        updateAssistant,
                        pushToolActivity,
                        setWorkflow,
                      });
                    } catch (parseErr) {
                      log.warn("Resume stream JSON parse error", trimmed.slice(0, 200), parseErr);
                    }
                  }
                }
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
    };
  }, [sessionId, refreshFiles, refreshSessionIndex]);

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

  /* ─── Auto-reconnect: when page becomes visible, resume lost stream ─── */
  useEffect(() => {
    const handleVisibility = async () => {
      if (document.visibilityState !== 'visible') return;
      if (!wasStreamingRef.current) return;
      if (readerRef.current) return;

      log.info("visibilitychange: page visible, resuming lost stream");
      setIsReconnecting(true);
      let retries = 0;
      while (retries < MAX_RETRIES) {
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
              const decoder = new TextDecoder();
              let buffer = "";

              const updateAssistant = (updater: (message: ChatMessage) => ChatMessage) => {
                setMessages((prev) => prev.map((msg) => (msg.id === inProgressId ? updater(msg) : msg)));
              };

              while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop() || "";
                for (const line of lines) {
                  const trimmed = line.trim();
                  if (!trimmed) continue;
                  try {
                    const data = JSON.parse(trimmed) as Record<string, any>;
                    applyStreamEvent(data, { updateAssistant, pushToolActivity, setWorkflow });
                  } catch { /* skip parse errors */ }
                }
              }
            }
          }
          break;
        } catch (err) {
          retries++;
          log.warn(`visibilitychange: resume attempt ${retries}/${MAX_RETRIES} failed`, err);
          if (retries >= MAX_RETRIES) break;
          await sleep(Math.min(1000 * Math.pow(2, retries), 30000));
        }
      }
      setIsReconnecting(false);
      wasStreamingRef.current = false;
    };

    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, [sessionId, pushToolActivity]);

  const SLASH_COMMANDS: Record<string, string> = {
    "/help": "显示所有可用命令",
    "/logs": "下载应用日志文件（zip）",
    "/files": "下载当前会话输出文件（zip）",
    "/clear": "清除当前会话记忆",
    "/status": "查看当前会话状态",
    "/config": "查看当前模型配置",
  };

  const handleSlashCommand = useCallback((cmd: string): boolean => {
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
        downloadLogsZip();
        break;
      }
      case "/files": {
        setMessages((prev) => [...prev, createSystemMessage("正在下载会话输出文件...")]);
        downloadSessionOutputs(sessionId);
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
          .then((res) => {
            const info = JSON.stringify(res, null, 2);
            setMessages((prev) => [...prev, createSystemMessage(`会话状态：\n${info}`)]);
          })
          .catch((err) => setMessages((prev) => [...prev, createSystemMessage(`获取状态失败: ${err.message}`)]));
        break;
      }
      case "/config": {
        fetch(buildApiUrl("/api/config"))
          .then((r) => r.json())
          .then((data) => {
            const info = JSON.stringify(data, null, 2);
            setMessages((prev) => [...prev, createSystemMessage(`模型配置：\n${info}`)]);
          })
          .catch((err) => setMessages((prev) => [...prev, createSystemMessage(`获取配置失败: ${err.message}`)]));
        break;
      }
    }
    return true;
  }, [sessionId]);

  const handleSubmit = useCallback(async () => {
    const content = inputValue.trim();
    if (!content || isStreaming) return;

    if (content.startsWith("/") && handleSlashCommand(content)) return;

    log.info("handleSubmit: sending message", content.slice(0, 80));
    const userMessage = createUserMessage(content);
    const assistantMessage = createAssistantMessage();
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setInputValue("");
    setIsStreaming(true);
    wasStreamingRef.current = true;
    let eventCount = 0;

    try {
      const response = await createChatRequest(sessionId, content, uploadedFiles.map((file) => file.id), assistantMessage.id);
      if (!response.ok || !response.body) {
        throw new Error(`Chat request failed: ${response.status}`);
      }

      const reader = response.body.getReader();
      readerRef.current = reader;
      const decoder = new TextDecoder();
      let buffer = "";

      const updateAssistant = (updater: (message: ChatMessage) => ChatMessage) => {
        setMessages((prev) => prev.map((message) => (message.id === assistantMessage.id ? updater(message) : message)));
      };

      log.info("SSE stream reading started");
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          log.info("SSE stream closed by server");
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            const data = JSON.parse(trimmed) as Record<string, any>;
            eventCount++;
            if (data.type === "image_generated" || data.type === "file_generated") {
              log.info(`[SSE] 收到artifact事件: type=${data.type}, filename=${data.filename}, image_url=${data.image_url || ''}, download_url=${data.download_url || ''}, isStreaming=${true}`);
            }
            if (data.type === "stream_end") {
              log.info(`[SSE] 收到stream_end事件, 此时isStreaming=true`);
            }
            applyStreamEvent(data, {
              updateAssistant,
              pushToolActivity,
              setWorkflow,
            });
          } catch (parseErr) {
            log.warn("SSE JSON parse error, line=", trimmed.slice(0, 200), parseErr);
          }
        }
      }
      log.info(`SSE stream ended, total events=${eventCount}`);
    } catch (error) {
      log.error("handleSubmit: stream error, retrying with backoff", error);
      let retries = 0;
      let resumed = false;
      while (retries < MAX_RETRIES && !resumed) {
        try {
          await sleep(Math.min(1000 * Math.pow(2, retries), 30000));
          const resumeResponse = await resumeStreamRequest(sessionId, eventCount);
          if (resumeResponse.ok && resumeResponse.body) {
            const resumeReader = resumeResponse.body.getReader();
            readerRef.current = resumeReader;
            const decoder = new TextDecoder();
            let resumeBuffer = "";
            const resumeUpdateAssistant = (updater: (message: ChatMessage) => ChatMessage) => {
              setMessages((prev) => prev.map((message) => (message.id === assistantMessage.id ? updater(message) : message)));
            };
            while (true) {
              const { done, value } = await resumeReader.read();
              if (done) break;
              resumeBuffer += decoder.decode(value, { stream: true });
              const lines = resumeBuffer.split("\n");
              resumeBuffer = lines.pop() || "";
              for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed) continue;
                try {
                  const data = JSON.parse(trimmed) as Record<string, any>;
                  applyStreamEvent(data, { updateAssistant: resumeUpdateAssistant, pushToolActivity, setWorkflow });
                } catch { /* skip parse errors */ }
              }
            }
            resumed = true;
            log.info("handleSubmit: resume succeeded after disconnect");
          } else {
            retries++;
          }
        } catch (retryErr) {
          retries++;
          log.warn(`handleSubmit: resume attempt ${retries}/${MAX_RETRIES} failed`, retryErr);
        }
      }
      if (!resumed) {
        log.warn("handleSubmit: all retries exhausted, showing error");
        setMessages((prev) => prev.map((message) => message.id === assistantMessage.id ? setAssistantFinalContent(message, "抱歉，连接中断，已尝试自动重连但未恢复。") : message));
      }
    } finally {
      setIsStreaming(false);
      wasStreamingRef.current = false;
      readerRef.current = null;
      log.info("handleSubmit: saving session");
      await saveSession(sessionId);
      await refreshSessionIndex();
      setTimeout(() => { refreshSessionIndex(); }, 5000);
      log.info("handleSubmit: complete");
    }
  }, [config, inputValue, isStreaming, pushToolActivity, refreshSessionIndex, sessionId, uploadedFiles]);

  const handleUpload = useCallback(async (file: File) => {
    await uploadFile(sessionId, file);
    await refreshFiles(sessionId);
  }, [refreshFiles, sessionId]);

  const handlePreviewFile = useCallback(async (fileId: string) => {
    const preview = await fetchFilePreview(sessionId, fileId);
    setSelectedPreview(preview);
  }, [sessionId]);

  const handleNewSession = useCallback(() => {
    const nextSessionId = generateSessionId();
    log.info("handleNewSession", nextSessionId);
    setSessionId(nextSessionId);
    setMessages([]);
    setUploadedFiles([]);
    setToolActivities([]);
    setWorkflow(null);
    setSelectedPreview(null);
    setRuntimeState({ isPaused: false });
  }, []);

  const handleDeleteSession = useCallback(async (targetSessionId: string) => {
    log.info("handleDeleteSession", targetSessionId);
    await deleteSession(targetSessionId);
    await refreshSessionIndex();
    if (targetSessionId === sessionId) {
      handleNewSession();
    }
  }, [handleNewSession, refreshSessionIndex, sessionId]);

  const toggleThought = useCallback((messageId: string, blockId: string) => {
    setMessages((prev) => prev.map((message) => {
      if (message.id !== messageId) return message;
      return {
        ...message,
        blocks: message.blocks.map((block) => block.id === blockId ? { ...block, isCollapsed: !block.isCollapsed } : block),
      };
    }));
  }, []);

  const updateAction = useCallback((callId: string, status: ActionDetail["status"], content: string) => {
    setMessages((prev) => prev.map((message) =>
      updateActionBlockStatus(message, callId, status, content)
    ));
  }, []);

  const pendingPermissionAsk = useMemo<PendingPermissionAsk | null>(() => {
    for (let mi = messages.length - 1; mi >= 0; mi--) {
      const msg = messages[mi];
      if (msg.role !== "FloodMind") continue;
      for (let bi = msg.blocks.length - 1; bi >= 0; bi--) {
        const block = msg.blocks[bi];
        if (block.type !== "action" || !block.actions) continue;
        for (let ai = block.actions.length - 1; ai >= 0; ai--) {
          const action = block.actions[ai];
          if (action.status === "pending_confirmation" && action.askId) {
            return {
              askId: action.askId,
              callId: action.callId,
              toolName: action.toolName,
              askReason: action.askReason || "",
              sessionId: action.sessionId || "",
            };
          }
        }
      }
    }
    return null;
  }, [messages]);

  const handleRespondPermissionAsk = useCallback(async (approved: boolean) => {
    if (!pendingPermissionAsk) return;
    const { askId, callId, sessionId } = pendingPermissionAsk;
    try {
      const data = await respondPermissionAskApi(askId, approved, sessionId);
      if (data.status === "success") {
        updateAction(callId, approved ? "running" : "error", approved ? "" : "用户拒绝");
      } else {
        updateAction(callId, "error", data.message || "确认失败");
      }
    } catch (err: any) {
      updateAction(callId, "error", `请求失败: ${err.message}`);
    }
  }, [pendingPermissionAsk, updateAction]);

  const sessionItems = useMemo(() => sessions, [sessions]);

  const handlePauseResume = useCallback(async () => {
    if (runtimeState.isPaused) {
      log.info("handlePauseResume: resuming");
      await resumeSession(sessionId);
      setRuntimeState({ isPaused: false });
      setIsStreaming(false);
      return;
    }

    log.info("handlePauseResume: pausing");
    await pauseSession(sessionId);
    readerRef.current?.cancel().catch(() => undefined);
    readerRef.current = null;
    setIsStreaming(false);
    setRuntimeState({ isPaused: true });
  }, [runtimeState.isPaused, sessionId]);

  return {
    sessionId,
    sessions: sessionItems,
    messages,
    uploadedFiles,
    toolActivities,
    workflow,
    selectedPreview,
    runtimeState,
    inputValue,
    isStreaming,
    isReconnecting,
    availableModels,
    setInputValue,
    handleSubmit,
    handleUpload,
    handlePreviewFile,
    handlePauseResume,
    handleNewSession,
    handleDeleteSession,
    loadSession,
    toggleThought,
    updateAction,
    pendingPermissionAsk,
    handleRespondPermissionAsk,
    closePreview: () => setSelectedPreview(null),
    config,
    setConfig: async (nextConfig: SessionConfig) => {
      setConfig(nextConfig);
      await updateSessionConfig(sessionId, nextConfig);
    },
  };
}
