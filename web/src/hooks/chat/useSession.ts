import { useCallback, useEffect, useState } from "react";
import { fetchSessions } from "@/api/agent";
import { createLogger } from "@/lib/logger";
import type { SessionSummary } from "@/types/app";
import { SESSION_STORAGE_KEY, generateSessionId } from "@/features/chat/lib/session-utils";

const log = createLogger("Session");

/**
 * 会话身份与列表：sessionId（localStorage 持久化）、sessions 列表、刷新、切换。
 * 注意：真正加载会话消息/恢复流的工作在 useChatStream 的 session-init effect 中完成，
 * 这里只负责身份切换（触发那里的 effect）。
 */
export function useSession() {
  const [sessionId, setSessionId] = useState<string>(() => {
    const stored = localStorage.getItem(SESSION_STORAGE_KEY) || generateSessionId();
    log.info("初始化 sessionId=", stored);
    return stored;
  });
  const [sessions, setSessions] = useState<SessionSummary[]>([]);

  useEffect(() => {
    localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
  }, [sessionId]);

  const refreshSessionIndex = useCallback(async () => {
    const items = await fetchSessions();
    setSessions(items);
  }, []);

  const loadSession = useCallback(
    (targetSessionId: string) => {
      if (targetSessionId === sessionId) return;
      log.info("loadSession: switching to", targetSessionId);
      setSessionId(targetSessionId);
      localStorage.setItem(SESSION_STORAGE_KEY, targetSessionId);
    },
    [sessionId]
  );

  return { sessionId, setSessionId, sessions, refreshSessionIndex, loadSession };
}
