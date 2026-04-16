import type { ChatMessage, GeneratedArtifact, MessageBlock } from "@/types/app";

export function createUserMessage(content: string): ChatMessage {
  return {
    id: crypto.randomUUID(),
    role: "user",
    content,
    timestamp: new Date().toISOString(),
    blocks: [
      {
        id: crypto.randomUUID(),
        type: "answer",
        content,
      },
    ],
  };
}

export function createAssistantMessage(id?: string): ChatMessage {
  return {
    id: id || crypto.randomUUID(),
    role: "assistant",
    content: "",
    timestamp: new Date().toISOString(),
    blocks: [],
    artifacts: [],
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
      id: crypto.randomUUID(),
      type: "thought",
      content,
      isCollapsed: false,
      isStreaming: true,
      isArchived: false,
    });
  }

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
      id: crypto.randomUUID(),
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
        : block,
    ),
  };
}

export function setAssistantFinalContent(message: ChatMessage, content: string): ChatMessage {
  const answerBlock: MessageBlock = {
    id: crypto.randomUUID(),
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

export function attachArtifact(message: ChatMessage, artifact: GeneratedArtifact): ChatMessage {
  const artifacts = [...(message.artifacts || [])];
  if (!artifacts.find((item) => item.filepath === artifact.filepath && item.filename === artifact.filename)) {
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
        id: crypto.randomUUID(),
        type: "thought",
        content: reasoning,
        isCollapsed: true,
        isArchived: true,
      });
    }
    if (content.trim()) {
      blocks.push({ id: crypto.randomUUID(), type: "answer", content, isArchived: false });
    }
  } else {
    blocks.push({ id: crypto.randomUUID(), type: "answer", content, isArchived: false });
  }

  return {
    id: crypto.randomUUID(),
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
