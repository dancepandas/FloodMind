import type { ChatMessage, GeneratedArtifact, MessageBlock, ActionDetail } from "@/types/app";
import { uuid } from "@/lib/utils";

const TOOL_DISPLAY_NAMES: Record<string, string> = {
  Bash: "Bash",
  Glob: "Glob",
  Grep: "Grep",
  Read: "Read",
  Write: "Write",
  Edit: "Edit",
  GetSkill: "GetSkill",
  KnowledgeSearch: "KnowledgeSearch",
  KnowledgeAdd: "KnowledgeAdd",
  WebSearch: "WebSearch",
  WebFetch: "WebFetch",
  MemorySearch: "MemorySearch",
  MemoryAdd: "MemoryAdd",
  CreatePlan: "CreatePlan",
  UpdateProjectInstructions: "UpdateProjectInstructions",
  CreateScheduledTask: "CreateScheduledTask",
  ListScheduledTasks: "ListScheduledTasks",
  CancelScheduledTask: "CancelScheduledTask",
  SubAgent: "SubAgent",
  ParallelSubAgent: "ParallelSubAgent",
  context_compress: "ContextCompress",
};

export function getToolDisplayName(toolName: string): string {
  return TOOL_DISPLAY_NAMES[toolName] || toolName;
}

export function createUserMessage(content: string): ChatMessage {
  return {
    id: uuid(),
    role: "human",
    content,
    timestamp: new Date().toISOString(),
    blocks: [
      {
        id: uuid(),
        type: "answer",
        content,
      },
    ],
  };
}

export function createAssistantMessage(id?: string): ChatMessage {
  return {
    id: id || uuid(),
    role: "FloodMind",
    content: "",
    isComplete: false,
    timestamp: new Date().toISOString(),
    blocks: [],
    artifacts: [],
  };
}

export function createSystemMessage(content: string): ChatMessage {
  return {
    id: uuid(),
    role: "system",
    content,
    timestamp: new Date().toISOString(),
    blocks: [
      {
        id: uuid(),
        type: "answer",
        content,
      },
    ],
  };
}

export function appendThoughtBlock(message: ChatMessage, content: string, append = true): ChatMessage {
  const normalized = String(content || "").trim();
  if (!normalized) return message;

  const blocks = message.blocks.map((b) => ({ ...b }));
  const last = blocks[blocks.length - 1];
  if (append && last?.type === "thought") {
    last.content += content;
    last.isCollapsed = false;
    last.isStreaming = true;
  } else {
    blocks.forEach((block) => {
      block.isArchived = true;
      if (block.type === "thought") {
        block.isCollapsed = true;
        block.isStreaming = false;
      }
    });
    blocks.push({
      id: uuid(),
      type: "thought",
      content,
      isCollapsed: false,
      isStreaming: true,
      isArchived: false,
    });
  }

  trimVisibleBlocks(blocks);
  return { ...message, blocks };
}

export function appendAnswerBlock(message: ChatMessage, content: string, append = true): ChatMessage {
  const normalized = String(content || "");
  const blocks = message.blocks.map((b) => ({ ...b }));

  blocks.forEach((block) => {
    if (block.type === "thought") {
      block.isCollapsed = true;
      block.isStreaming = false;
      block.isArchived = true;
    }
  });

  const lastAnswerIdx = blocks.map((b, i) => b.type === "answer" ? i : -1).filter(i => i >= 0).pop();
  const hasNewPhaseAfterAnswer = lastAnswerIdx !== undefined
    ? blocks.slice(lastAnswerIdx + 1).some((b) => b.type === "thought" || b.type === "action")
    : false;

  if (append && blocks[blocks.length - 1]?.type === "answer" && !hasNewPhaseAfterAnswer) {
    blocks[blocks.length - 1].content += normalized;
  } else {
    blocks.forEach((block) => {
      if (block.type === "answer") {
        block.isArchived = true;
        block.isCollapsed = true;
      }
    });
    blocks.push({
      id: uuid(),
      type: "answer",
      content: normalized,
      isArchived: false,
    });
  }

  return {
    ...message,
    content: getMessageAnswerText(blocks),
    blocks,
  };
}

const MAX_VISIBLE_BLOCKS = 5;

function trimVisibleBlocks(blocks: MessageBlock[]): void {
  const visibleIndices: number[] = [];
  blocks.forEach((b, i) => {
    if ((b.type === "thought" || b.type === "action") && !b.isArchived) {
      visibleIndices.push(i);
    }
  });
  const excess = visibleIndices.length - MAX_VISIBLE_BLOCKS;
  if (excess <= 0) return;
  for (let i = 0; i < excess; i++) {
    const idx = visibleIndices[i];
    blocks[idx].isArchived = true;
    blocks[idx].isCollapsed = true;
    blocks[idx].isStreaming = false;
  }
}

function actionLabel(action: ActionDetail): string {
  const isSubAgent = action.toolName === "SubAgent" || action.toolName === "ParallelSubAgent" || action.toolName === "ParallelTask";
  if (isSubAgent) return "SubAgent";
  return action.delegation?.label || getToolDisplayName(action.toolName);
}

function findActionByCallId(actions: ActionDetail[], callId: string): number {
  return actions.findIndex((a) => a.callId === callId);
}

function findActionByToolNameRunning(actions: ActionDetail[], toolName: string): number {
  return actions.findIndex((a) => a.toolName === toolName && a.status === "running");
}

export function appendActionBlock(message: ChatMessage, toolName: string, status: ActionDetail["status"], content: string, delegation?: ActionDetail["delegation"], callId?: string, askId?: string, askReason?: string, askSessionId?: string): ChatMessage {
  const blocks = message.blocks.map((b) => {
    const copy: MessageBlock = { ...b };
    if (b.actions) {
      copy.actions = b.actions.map((a) => ({ ...a, delegation: a.delegation ? { ...a.delegation } : undefined }));
    }
    return copy;
  });

  const last = blocks[blocks.length - 1];
  if (last?.type === "thought") {
    last.isCollapsed = true;
    last.isStreaming = false;
  }

  const effectiveCallId = callId || `${toolName}-${Date.now()}`;

  if (status === "running" || status === "pending_confirmation") {
    const action: ActionDetail = {
      callId: effectiveCallId,
      toolName,
      status,
      content: "",
      delegation,
      askId,
      askReason,
      sessionId: askSessionId,
    };

    const existingActionBlockIdx = blocks.findIndex(
      (b) => b.type === "action" && b.actions?.some((a) => a.callId === effectiveCallId)
    );
    if (existingActionBlockIdx >= 0) {
      return { ...message, blocks };
    }

    const lastAction = [...blocks].reverse().find((b) => b.type === "action" && !b.isArchived);
    if (lastAction && !lastAction.isArchived) {
      lastAction.actions = [...(lastAction.actions || []), action];
      lastAction.content = lastAction.actions.map((a) => `▸ ${actionLabel(a)}`).join("\n");
      lastAction.isStreaming = lastAction.actions.some((a) => a.status === "running" || a.status === "pending_confirmation");
      trimVisibleBlocks(blocks);
      return { ...message, blocks };
    }

    blocks.push({
      id: uuid(),
      type: "action",
      content: `▸ ${actionLabel(action)}`,
      actions: [action],
      isCollapsed: false,
      isStreaming: true,
      isArchived: false,
    });
    trimVisibleBlocks(blocks);
    return { ...message, blocks };
  }

  if (status === "done" || status === "error") {
    const actionBlockIdx = blocks.findIndex(
      (b) => b.type === "action" && b.actions?.some((a) => a.callId === effectiveCallId)
    );

    if (actionBlockIdx < 0 && callId) {
      const fallbackIdx = blocks.findIndex(
        (b) => b.type === "action" && b.actions?.some((a) => a.toolName === toolName && a.status === "running")
      );
      if (fallbackIdx >= 0) {
        return _updateActionBlock(message, blocks, fallbackIdx, toolName, status, content, delegation, effectiveCallId);
      }
    }

    if (actionBlockIdx >= 0) {
      return _updateActionBlock(message, blocks, actionBlockIdx, toolName, status, content, delegation, effectiveCallId);
    }

    return { ...message, blocks };
  }

  return { ...message, blocks };
}

function _recomputeActionBlockState(block: MessageBlock): void {
  const actions = block.actions || [];
  const allDone = actions.every((a) => a.status !== "running" && a.status !== "pending_confirmation");
  block.isStreaming = !allDone;
  block.isArchived = allDone;
  const doneCount = actions.filter((a) => a.status === "done").length;
  const errCount = actions.filter((a) => a.status === "error").length;
  block.content = actions
    .map((a) => {
      const icon = a.status === "running" ? "▸" : a.status === "pending_confirmation" ? "⏳" : a.status === "done" ? "✓" : "✗";
      return `${icon} ${actionLabel(a)}`;
    })
    .join("\n");
  if (allDone) {
    const labelParts: string[] = [];
    if (doneCount > 0) labelParts.push(`${doneCount}项完成`);
    if (errCount > 0) labelParts.push(`${errCount}项失败`);
    block.content = `[${labelParts.join(", ")}] ` + block.content;
  }
}

function _updateActionBlock(message: ChatMessage, blocks: MessageBlock[], blockIdx: number, toolName: string, status: ActionDetail["status"], content: string, delegation: ActionDetail["delegation"] | undefined, callId: string): ChatMessage {
  const actionBlock = blocks[blockIdx];
  const isSubAgent = toolName === "SubAgent" || toolName === "ParallelSubAgent" || toolName === "ParallelTask";
  const updatedActions = (actionBlock.actions || []).map((a) => {
    if (a.callId === callId || (a.toolName === toolName && a.status === "running")) {
      const updatedDelegation = delegation
        ? { ...a.delegation, ...delegation, summary: delegation.summary || a.delegation?.summary }
        : a.delegation;
      return {
        ...a,
        callId: a.callId || callId,
        status,
        content: isSubAgent ? "" : (status === "error" ? content : content.slice(0, 200)),
        delegation: isSubAgent ? { task: "", label: "SubAgent", skill_name: "" } : updatedDelegation,
      };
    }
    return a;
  });

  actionBlock.actions = updatedActions;
  _recomputeActionBlockState(actionBlock);

  trimVisibleBlocks(blocks);
  return { ...message, blocks };
}

export function finalizeThoughtBlocks(message: ChatMessage): ChatMessage {
  const lastAnswer = [...message.blocks].reverse().find((block) => block.type === "answer" && block.content.trim());
  const lastAnswerId = lastAnswer?.id;
  const blocks = message.blocks.map((block) => {
    if (block.type === "thought") {
      return { ...block, isCollapsed: true, isStreaming: false, isArchived: true };
    }
    if (block.type === "action") {
      return { ...block, isStreaming: false, isCollapsed: true, isArchived: true };
    }
    if (block.type === "answer" && block.id !== lastAnswerId) {
      return { ...block, isArchived: true, isCollapsed: true };
    }
    if (block.type === "answer" && block.id === lastAnswerId) {
      return { ...block, isArchived: false };
    }
    return block;
  });

  return {
    ...message,
    isComplete: true,
    content: lastAnswer ? lastAnswer.content : getMessageAnswerText(blocks),
    blocks,
  };
}

export function setAssistantFinalContent(message: ChatMessage, content: string): ChatMessage {
  const blocks = message.blocks.map((b) =>
    b.type === "answer" ? { ...b, isArchived: true, isCollapsed: true } : { ...b }
  );
  if (content.trim()) {
    blocks.push({
      id: uuid(),
      type: "answer",
      content,
      isArchived: false,
    });
  }
  return {
    ...message,
    isComplete: true,
    content: content.trim() ? content : getMessageAnswerText(blocks),
    blocks,
  };
}

function artifactKey(artifact: GeneratedArtifact): string {
  if (artifact.download_url) return artifact.download_url;
  if (artifact.image_url) return artifact.image_url;
  if (artifact.filepath) return `${artifact.filepath}:${artifact.filename}`;
  return `${artifact.type}:${artifact.filename}`;
}

export function attachArtifact(message: ChatMessage, artifact: GeneratedArtifact): ChatMessage {
  const artifacts = [...(message.artifacts || [])];
  const key = artifactKey(artifact);
  if (!artifacts.find((item) => artifactKey(item) === key)) {
    artifacts.push(artifact);
  }
  return { ...message, artifacts };
}

export function fromServerMessage(raw: Record<string, unknown>): ChatMessage {
  const rawRole = String(raw.role || "FloodMind");
  const role = (rawRole === "user" ? "human" : rawRole === "assistant" ? "FloodMind" : rawRole) as ChatMessage["role"];
  const content = String(raw.content || "");
  const reasoning = String(raw.reasoning || "");
  const blocks: MessageBlock[] = [];

  if (role === "FloodMind") {
    if (reasoning.trim()) {
      blocks.push({
        id: uuid(),
        type: "thought",
        content: reasoning,
        isCollapsed: true,
        isArchived: true,
      });
    }
    const toolCalls = raw.tool_calls as Array<Record<string, unknown>> | undefined;
    if (toolCalls && Array.isArray(toolCalls)) {
      const actions: ActionDetail[] = toolCalls.map((tc) => ({
        callId: String(tc.call_id || tc.tool_call_id || uuid()),
        toolName: String(tc.tool_name || ""),
        status: "done" as const,
        content: String(tc.tool_output || "").slice(0, 200),
      }));
      const contentLines = actions.map((a) => `✓ ${getToolDisplayName(a.toolName)}`).join("\n");
      blocks.push({
        id: uuid(),
        type: "action",
        content: `[${actions.length}项完成] ${contentLines}`,
        actions,
        isCollapsed: true,
        isStreaming: false,
        isArchived: true,
      });
    }
    if (content.trim()) {
      blocks.push({ id: uuid(), type: "answer", content, isArchived: false });
    }
  } else {
    blocks.push({ id: uuid(), type: "answer", content, isArchived: false });
  }

  return {
    id: uuid(),
    role,
    content,
    reasoning,
    isComplete: true,
    timestamp: new Date().toISOString(),
    blocks,
    artifacts: [],
  };
}

function getMessageAnswerText(blocks: MessageBlock[]): string {
  return blocks.filter((block) => block.type === "answer" && !block.isArchived).map((block) => block.content).join("\n\n");
}

export function updateActionBlockStatus(
  message: ChatMessage,
  callId: string,
  status: ActionDetail["status"],
  content: string,
  extra?: { askId?: string; askReason?: string; sessionId?: string },
): ChatMessage {
  const blocks = message.blocks.map((b) => {
    const copy: MessageBlock = { ...b };
    if (b.actions) {
      copy.actions = b.actions.map((a) => ({ ...a, delegation: a.delegation ? { ...a.delegation } : undefined }));
    }
    return copy;
  });

  let found = false;
  for (const block of blocks) {
    if (block.type !== "action" || !block.actions) continue;
    const idx = block.actions.findIndex((a) => a.callId === callId);
    if (idx < 0) continue;
    found = true;
    const action = block.actions[idx];
    const clearAsk = status !== "pending_confirmation";
    block.actions[idx] = {
      ...action,
      status,
      content: content || action.content,
      askId: clearAsk ? undefined : (extra?.askId ?? action.askId),
      askReason: clearAsk ? undefined : (extra?.askReason ?? action.askReason),
      sessionId: clearAsk ? undefined : (extra?.sessionId ?? action.sessionId),
    };
    _recomputeActionBlockState(block);
    break;
  }

  if (!found) return { ...message };
  return { ...message, blocks };
}
