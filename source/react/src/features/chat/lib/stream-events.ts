import {
  appendAnswerBlock,
  appendThoughtBlock,
  attachArtifact,
  finalizeThoughtBlocks,
  setAssistantFinalContent,
} from "@/features/chat/lib/message-blocks";
import type { ChatMessage, GeneratedArtifact, ToolActivity, WorkflowPlan } from "@/types/app";

interface StreamHandlers {
  updateAssistant: (updater: (message: ChatMessage) => ChatMessage) => void;
  pushToolActivity: (toolName: string, content: string, status: ToolActivity["status"]) => void;
  setWorkflow: (updater: WorkflowPlan | ((prev: WorkflowPlan | null) => WorkflowPlan | null)) => void;
}

export function applyStreamEvent(data: Record<string, any>, handlers: StreamHandlers) {
  const { updateAssistant, pushToolActivity, setWorkflow } = handlers;

  if (data.type === "reasoning") {
    updateAssistant((message) => appendThoughtBlock(message, data.content || "", true));
    return;
  }

  if (data.type === "thought_summary") {
    updateAssistant((message) => appendThoughtBlock(message, data.content || "", false));
    return;
  }

  if (data.type === "workflow_plan") {
    setWorkflow({
      title: data.title || "调度计划",
      steps: (data.steps || []).map((step: Record<string, any>, index: number) => ({
        key: step.key || step.step_key || `${index}`,
        label: step.label || step.title || step.detail || `步骤 ${index + 1}`,
        title: step.title || step.label || step.detail || "",
        status: step.status || "pending",
        detail: step.detail || "",
        outcome: step.outcome || "",
      })),
    });
    return;
  }

  if (data.type === "workflow_step") {
    setWorkflow((prev) => {
      const steps = [...(prev?.steps || [])];
      const idx = steps.findIndex((step) => step.key === data.step_key);
      const next = {
        key: data.step_key || crypto.randomUUID(),
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
    pushToolActivity(data.tool_name || "tool", data.content || "", data.status === "error" ? "error" : "running");
    return;
  }

  if (data.type === "tool_result") {
    pushToolActivity(data.tool_name || "tool", data.content || "", "done");
    return;
  }

  if (data.type === "file_generated" || data.type === "image_generated") {
    updateAssistant((message) => attachArtifact(message, data as GeneratedArtifact));
    return;
  }

  if (data.type === "final_override") {
    updateAssistant((message) => setAssistantFinalContent(finalizeThoughtBlocks(message), data.content || ""));
    return;
  }

  if (data.type === "stream_end") {
    updateAssistant((message) => finalizeThoughtBlocks(message));
    return;
  }

  if (data.content) {
    updateAssistant((message) => appendAnswerBlock(message, data.content || "", data.type === "token"));
  }
}
