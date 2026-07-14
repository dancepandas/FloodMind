import { useCallback, useMemo } from "react";
import { respondPermissionAsk as respondPermissionAskApi } from "@/api/agent";
import { updateActionBlockStatus } from "@/features/chat/lib/message-blocks";
import { createLogger } from "@/lib/logger";
import type { ActionDetail, ChatMessage, PendingPermissionAsk } from "@/types/app";

const log = createLogger("Permission");

export type SetMessages = React.Dispatch<React.SetStateAction<ChatMessage[]>>;

/**
 * 从最新消息向前找一条 status===pending_confirmation 且带 askId 的 action。
 * 纯函数：便于单测，并让外层 useMemo 保持简单、可被 React Compiler 保留。
 */
function derivePendingPermissionAsk(messages: ChatMessage[]): PendingPermissionAsk | null {
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
            toolInput: action.toolInput,
          };
        }
      }
    }
  }
  return null;
}

/**
 * 权限审批：从消息流派生当前"待确认"的 ASK，并提供响应入口。
 * updateAction 仅内部使用（响应后把对应 action 置 running/error），故不对外暴露——
 * 同时这也移除了原先向 ChatMessage 透传的死 prop onUpdateAction。
 */
export function usePermission(messages: ChatMessage[], setMessages: SetMessages) {
  const pendingPermissionAsk = useMemo<PendingPermissionAsk | null>(
    () => derivePendingPermissionAsk(messages),
    [messages]
  );

  const updateAction = useCallback(
    (callId: string, status: ActionDetail["status"], content: string) => {
      setMessages((prev) => prev.map((message) => updateActionBlockStatus(message, callId, status, content)));
    },
    [setMessages]
  );

  const handleRespondPermissionAsk = useCallback(
    async (approved: boolean) => {
      if (!pendingPermissionAsk) return;
      const { askId, callId, sessionId } = pendingPermissionAsk;
      try {
        const data = await respondPermissionAskApi(askId, approved, sessionId);
        if (data.status === "success") {
          updateAction(callId, approved ? "running" : "error", approved ? "" : "用户拒绝");
        } else {
          updateAction(callId, "error", data.message || "确认失败");
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        log.error("respondPermissionAsk failed", err);
        updateAction(callId, "error", `请求失败: ${msg}`);
      }
    },
    [pendingPermissionAsk, updateAction]
  );

  return { pendingPermissionAsk, handleRespondPermissionAsk };
}
