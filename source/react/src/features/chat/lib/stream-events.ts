import {
  appendAnswerBlock,
  appendThoughtBlock,
  attachArtifact,
  finalizeThoughtBlocks,
  setAssistantFinalContent,
} from "@/features/chat/lib/message-blocks";
import { createLogger } from "@/lib/logger";
import { uuid } from "@/lib/utils";
import type { ChatMessage, GeneratedArtifact, ToolActivity, WorkflowPlan } from "@/types/app";

const log = createLogger("Stream");

interface StreamHandlers {
  updateAssistant: (updater: (message: ChatMessage) => ChatMessage) => void;
  pushToolActivity: (toolName: string, content: string, status: ToolActivity["status"]) => void;
  setWorkflow: (updater: WorkflowPlan | ((prev: WorkflowPlan | null) => WorkflowPlan | null)) => void;
}

export function applyStreamEvent(data: Record<string, any>, handlers: StreamHandlers) {
  const { updateAssistant, pushToolActivity, setWorkflow } = handlers;
  const eventType = data.type || "(no type)";

  if (data.type === "reasoning") {
    log.debug(`[${eventType}] len=${(data.content || "").length}`);
    updateAssistant((message) => appendThoughtBlock(message, data.content || "", true));
    return;
  }

  if (data.type === "thought_summary") {
    log.debug(`[${eventType}] len=${(data.content || "").length}`);
    updateAssistant((message) => appendThoughtBlock(message, data.content || "", false));
    return;
  }

  if (data.type === "workflow_plan") {
    const stepCount = (data.steps || []).length;
    log.info(`[${eventType}] title="${data.title}" steps=${stepCount}`);
    setWorkflow({
      title: data.title || "调度计划",
      steps: (data.steps || []).map((step: Record<string, any>, index: number) => ({
        key: step.key || step.step_key || `${index}`,
        label: step.label || step.title || step.detail || `步骤 ${index + 1}`,
        title: step.title || step.label || step.detail || "",
        status: step.status || "pending",
        detail: step.detail || "",
        outcome: step.outcome || "",
        expected_deliverables: step.expected_deliverables || [],
        output_artifacts: step.output_artifacts || [],
      })),
    });
    return;
  }

  if (data.type === "workflow_step") {
    log.info(`[${eventType}] step_key="${data.step_key}" status="${data.status}" label="${data.label || data.title || ""}"`);
    setWorkflow((prev) => {
      const steps = [...(prev?.steps || [])];
      const idx = steps.findIndex((step) => step.key === data.step_key);
      const next = {
        key: data.step_key || uuid(),
        label: data.label || data.title || data.detail || "步骤",
        title: data.title || data.detail || data.label || "",
        status: data.status || "pending",
        detail: data.detail || "",
        outcome: data.outcome || "",
      };
      if (idx >= 0) steps[idx] = { ...steps[idx], ...next };
      else steps.push(next);
      return { title: prev?.title || "调度计划", steps };
    });
    return;
  }

  if (data.type === "tool_status") {
    const status = data.status === "error" ? "error" : "running";
    log.info(`[${eventType}] tool="${data.tool_name}" status="${status}"`);
    pushToolActivity(data.tool_name || "tool", data.content || "", status);
    return;
  }

  if (data.type === "tool_result") {
    const contentPreview = (data.content || "").slice(0, 120);
    log.info(`[${eventType}] tool="${data.tool_name}" content=${contentPreview.length > 0 ? `"${contentPreview}…"` : "(empty)"}`);
    pushToolActivity(data.tool_name || "tool", data.content || "", "done");
    return;
  }

  if (data.type === "file_generated" || data.type === "image_generated") {
    log.info(`[${eventType}] filename="${data.filename}" filepath="${data.filepath}" image_url="${data.image_url || ''}" download_url="${data.download_url || ''}" size=${data.size}`);
    updateAssistant((message) => {
      const updated = attachArtifact(message, data as GeneratedArtifact);
      log.info(`[${eventType}] attachArtifact result: artifacts count=${updated.artifacts?.length}, last artifact type=${updated.artifacts?.[updated.artifacts.length - 1]?.type}, image_url=${updated.artifacts?.[updated.artifacts.length - 1]?.image_url}`);
      return updated;
    });
    return;
  }

  if (data.type === "final_override") {
    log.info(`[${eventType}] len=${(data.content || "").length}`);
    updateAssistant((message) => setAssistantFinalContent(finalizeThoughtBlocks(message), data.content || ""));
    return;
  }

  if (data.type === "stream_end") {
    log.info(`[${eventType}] stream complete`);
    updateAssistant((message) => finalizeThoughtBlocks(message));
    return;
  }

  if (data.content) {
    const preview = (data.content || "").slice(0, 80);
    log.debug(`[content] type="${data.type || "(none)"}" len=${(data.content || "").length} preview="${preview}…"`);
    updateAssistant((message) => appendAnswerBlock(message, data.content || "", data.type === "token"));
    return;
  }

  log.warn(`[unknown] type="${eventType}" no content, ignored`, data);
}
