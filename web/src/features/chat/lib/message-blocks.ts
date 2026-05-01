import type { ChatMessage, GeneratedArtifact, MessageBlock, ActionDetail } from "@/types/app";
import { uuid } from "@/lib/utils";

const TOOL_DISPLAY_NAMES: Record<string, string> = {
  run_script: "运行脚本",
  exec_bash: "执行命令",
  exec_python_file: "运行Python",
  write_text_file: "写入文件",
  knowledge_search: "知识检索",
  web_search: "网络搜索",
  add_memory: "记忆存储",
  search_memory: "记忆搜索",
  search_artifacts: "产物搜索",
  check_artifact_exists: "产物检查",
  read_artifact: "读取产物",
  create_plan: "创建计划",
  update_project_instructions: "更新指令",
  create_scheduled_task: "创建定时任务",
  list_scheduled_tasks: "查看定时任务",
  cancel_scheduled_task: "取消定时任务",
  delegate_execution_specialist: "委派执行",
  get_skill: "获取技能",
};

export function getToolDisplayName(toolName: string): string {
  return TOOL_DISPLAY_NAMES[toolName] || toolName;
}

export function createUserMessage(content: string): ChatMessage {
  return {
    id: uuid(),
    role: "user",
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
    role: "assistant",
    content: "",
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

  const blocks = [...message.blocks];
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
  const blocks = [...message.blocks];
  const last = blocks[blocks.length - 1];

  if (last?.type === "thought") {
    last.isCollapsed = true;
    last.isStreaming = false;
    last.isArchived = true;
  }

  if (append && blocks[blocks.length - 1]?.type === "answer") {
    blocks[blocks.length - 1].content += normalized;
  } else {
    blocks.forEach((block) => {
      if (block.type === "answer") {
        block.isArchived = true;
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
  return action.delegation?.label || getToolDisplayName(action.toolName);
}

export function appendActionBlock(message: ChatMessage, toolName: string, status: ActionDetail["status"], content: string, delegation?: ActionDetail["delegation"]): ChatMessage {
  const blocks = [...message.blocks];

  const last = blocks[blocks.length - 1];
  if (last?.type === "thought") {
    last.isCollapsed = true;
    last.isStreaming = false;
  }

  const existingActionIdx = blocks.findIndex(
    (b) => b.type === "action" && b.actions?.some((a) => a.toolName === toolName && a.status === "running")
  );

  if (status === "running") {
    const action: ActionDetail = { toolName, status: "running", content: "", delegation };
    if (existingActionIdx >= 0) {
      return { ...message, blocks };
    }

    const lastAction = [...blocks].reverse().find((b) => b.type === "action" && !b.isArchived);
    if (lastAction && !lastAction.isArchived) {
      lastAction.actions = [...(lastAction.actions || []), action];
      lastAction.content = lastAction.actions.map((a) => `▸ ${actionLabel(a)}`).join("\n");
      lastAction.isStreaming = lastAction.actions.some((a) => a.status === "running");
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
      (b) => b.type === "action" && b.actions?.some((a) => a.toolName === toolName && a.status === "running")
    );

    if (actionBlockIdx >= 0) {
      const actionBlock = blocks[actionBlockIdx];
      const updatedActions = (actionBlock.actions || []).map((a) => {
        if (a.toolName === toolName && a.status === "running") {
          const updatedDelegation = delegation
            ? { ...a.delegation, ...delegation, summary: delegation.summary || a.delegation?.summary }
            : a.delegation;
          return {
            ...a,
            status,
            content: status === "error" ? content : content.slice(0, 200),
            delegation: updatedDelegation,
          };
        }
        return a;
      });

      const allDone = updatedActions.every((a) => a.status !== "running");
      actionBlock.actions = updatedActions;
      actionBlock.isStreaming = !allDone;
      actionBlock.isArchived = allDone;

      const doneCount = updatedActions.filter((a) => a.status === "done").length;
      const errCount = updatedActions.filter((a) => a.status === "error").length;
      actionBlock.content = updatedActions
        .map((a) => {
          const icon = a.status === "running" ? "▸" : a.status === "done" ? "✓" : "✗";
          return `${icon} ${actionLabel(a)}`;
        })
        .join("\n");

      if (allDone) {
        const labelParts: string[] = [];
        if (doneCount > 0) labelParts.push(`${doneCount}项完成`);
        if (errCount > 0) labelParts.push(`${errCount}项失败`);
        actionBlock.content = `[${labelParts.join(", ")}] ` + actionBlock.content;
      }
    }

    trimVisibleBlocks(blocks);
    return { ...message, blocks };
  }

  return { ...message, blocks };
}

export function finalizeThoughtBlocks(message: ChatMessage): ChatMessage {
  const lastAnswer = [...message.blocks].reverse().find((block) => block.type === "answer" && block.content.trim());
  if (lastAnswer) {
    return {
      ...message,
      content: lastAnswer.content,
      blocks: [{ ...lastAnswer, isArchived: false }],
    };
  }

  return {
    ...message,
    blocks: message.blocks.map((block) =>
      block.type === "thought"
        ? { ...block, isCollapsed: true, isStreaming: false }
        : block.type === "action"
          ? { ...block, isStreaming: false, isCollapsed: true }
          : block,
    ),
  };
}

export function setAssistantFinalContent(message: ChatMessage, content: string): ChatMessage {
  const answerBlock: MessageBlock = {
    id: uuid(),
    type: "answer",
    content,
    isArchived: false,
  };
  return {
    ...message,
    content,
    blocks: content.trim() ? [answerBlock] : message.blocks,
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
  const role = String(raw.role || "assistant") as ChatMessage["role"];
  const content = String(raw.content || "");
  const reasoning = String(raw.reasoning || "");
  const blocks: MessageBlock[] = [];

  if (role === "assistant") {
    if (reasoning.trim()) {
      blocks.push({
        id: uuid(),
        type: "thought",
        content: reasoning,
        isCollapsed: true,
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
    timestamp: new Date().toISOString(),
    blocks,
    artifacts: [],
  };
}

function getMessageAnswerText(blocks: MessageBlock[]): string {
  return blocks.filter((block) => block.type === "answer").map((block) => block.content).join("\n\n");
}
