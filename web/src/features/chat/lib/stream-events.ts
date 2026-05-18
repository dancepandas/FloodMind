import {
  appendActionBlock,
  appendAnswerBlock,
  appendThoughtBlock,
  attachArtifact,
  finalizeThoughtBlocks,
  setAssistantFinalContent,
  updateActionBlockStatus,
} from "@/features/chat/lib/message-blocks";
import { createLogger } from "@/lib/logger";
import type { ChatMessage, GeneratedArtifact, ReferenceLink, ToolActivity, WorkflowPlan } from "@/types/app";

const log = createLogger("Stream");

function normalizeWorkflowStatus(raw: string): "pending" | "running" | "completed" | "error" {
  if (raw === "completed" || raw === "running" || raw === "pending" || raw === "error") return raw;
  if (raw === "done") return "completed";
  return "pending";
}

function parseKnowledgeReferences(content: string): ReferenceLink[] {
  const refs: ReferenceLink[] = [];
  const pattern = /【参考\s*\d+】\s*\(来源:\s*([^|)]+)\|/g;
  let match;
  while ((match = pattern.exec(content)) !== null) {
    const source = match[1].trim();
    const filename = source.split("/").pop() || source;
    refs.push({ title: filename, source });
  }
  return refs;
}

function parseWebReferences(content: string): ReferenceLink[] {
  const refs: ReferenceLink[] = [];
  try {
    const items = JSON.parse(content);
    if (Array.isArray(items)) {
      for (const item of items) {
        if (item.url || item.title) {
          refs.push({
            title: item.title || item.url || "",
            url: item.url,
            source: item.source || item.website || "",
          });
        }
      }
    }
  } catch {
    const urlPattern = /"url"\s*:\s*"([^"]+)"/g;
    const titlePattern = /"title"\s*:\s*"([^"]+)"/g;
    const urls: string[] = [];
    const titles: string[] = [];
    let m;
    while ((m = urlPattern.exec(content)) !== null) urls.push(m[1]);
    while ((m = titlePattern.exec(content)) !== null) titles.push(m[1]);
    const count = Math.max(urls.length, titles.length);
    for (let i = 0; i < count; i++) {
      refs.push({ title: titles[i] || urls[i] || "", url: urls[i] });
    }
  }
  return refs;
}

interface StreamHandlers {
  updateAssistant: (updater: (message: ChatMessage) => ChatMessage) => void;
  pushToolActivity: (toolName: string, content: string, status: ToolActivity["status"]) => void;
  setWorkflow: (updater: WorkflowPlan | ((prev: WorkflowPlan | null) => WorkflowPlan | null)) => void;
}

export function applyStreamEvent(data: Record<string, any>, handlers: StreamHandlers) {
  const { updateAssistant, pushToolActivity, setWorkflow } = handlers;
  const eventType = data.type || "(no type)";

  if (data.type === "heartbeat") {
    return;
  }

  if (data.type === "thought_delta" || data.type === "reasoning") {
    log.debug(`[${eventType}] len=${(data.content || "").length}`);
    updateAssistant((message) => appendThoughtBlock(message, data.content || "", true));
    return;
  }

  if (data.type === "thought_summary") {
    log.debug(`[${eventType}] len=${(data.content || "").length}`);
    updateAssistant((message) => appendThoughtBlock(message, data.content || "", false));
    return;
  }

  if (data.type === "answer_delta" || data.type === "token") {
    log.debug(`[${eventType}] len=${(data.content || "").length}`);
    updateAssistant((message) => appendAnswerBlock(message, data.content || "", true));
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
        status: normalizeWorkflowStatus(step.status || "pending"),
        detail: step.detail || "",
        outcome: step.outcome || "",
        expected_deliverables: step.expected_deliverables || [],
        output_artifacts: step.output_artifacts || [],
      })),
    });
    return;
  }

  if (data.type === "workflow_step") {
    const stepKey = data.step_key || "";
    const rawStatus = data.status || "pending";
    const normalizedStatus = normalizeWorkflowStatus(rawStatus);
    log.info(`[${eventType}] step_key="${stepKey}" status="${rawStatus}" -> "${normalizedStatus}"`);
    setWorkflow((prev) => {
      const steps = [...(prev?.steps || [])];
      const idx = steps.findIndex((step) => step.key === stepKey);
      if (idx < 0) {
        log.warn(`[${eventType}] unknown step_key="${stepKey}", ignoring`);
        return prev || { title: "调度计划", steps: [] };
      }
      steps[idx] = { ...steps[idx], status: normalizedStatus };
      return { title: prev?.title || "调度计划", steps };
    });
    return;
  }

if (data.type === "action_start" || data.type === "tool_status") {
    const status = data.status === "error" ? "error" : "running";
    const toolName = data.tool_name || "tool";
    const callId = data.call_id || "";
    log.info(`[${eventType}] tool="${toolName}" call_id="${callId}" status="${status}"`);
    pushToolActivity(toolName, data.content || "", status);
    const delegation = data.delegation || undefined;
    updateAssistant((message) => appendActionBlock(message, toolName, status, data.content || "", delegation, callId));
    return;
  }

  if (data.type === "permission_ask") {
    const askId = data.ask_id || "";
    const toolName = data.tool_name || "tool";
    const askReason = data.reason || "";
    const askSessionId = data.session_id || "";
    const callId = data.call_id || (askId ? `ask-${askId}` : "");
    log.info(`[${eventType}] permission_ask ask_id="${askId}" tool="${toolName}" call_id="${callId}" reason="${askReason}"`);
    pushToolActivity(toolName, askReason, "pending_confirmation");
    if (callId) {
      updateAssistant((message) =>
        updateActionBlockStatus(message, callId, "pending_confirmation", askReason, { askId, askReason, sessionId: askSessionId })
      );
    } else {
      updateAssistant((message) =>
        appendActionBlock(message, toolName, "pending_confirmation", askReason, undefined, "", askId, askReason, askSessionId)
      );
    }
    return;
  }

  if (data.type === "permission_resolved") {
    const callId = data.call_id || "";
    const approved = !!data.approved;
    log.info(`[${eventType}] call_id="${callId}" approved=${approved}`);
    if (callId) {
      updateAssistant((message) =>
        updateActionBlockStatus(message, callId, approved ? "running" : "error", approved ? "" : "权限被拒绝")
      );
    }
    return;
  }

  if (data.type === "action_end" || data.type === "tool_result") {
    const contentPreview = (data.content || "").slice(0, 120);
    const toolName = data.tool_name || "tool";
    const callId = data.call_id || "";
    const rawContent = data.content || "";
    log.info(`[${eventType}] tool="${toolName}" call_id="${callId}" content=${contentPreview.length > 0 ? `"${contentPreview}…"` : "(empty)"}`);
    pushToolActivity(toolName, rawContent, "done");
    const delegation = data.delegation || undefined;
    updateAssistant((message) => appendActionBlock(message, toolName, "done", rawContent, delegation, callId));

    let refs: ReferenceLink[] | null = null;
    if (toolName === "knowledge_search") {
      refs = parseKnowledgeReferences(rawContent);
    } else if (toolName === "web_search") {
      refs = parseWebReferences(rawContent);
    }
    if (refs !== null) {
      const seen = new Set<string>();
      const deduped = refs.filter((r) => {
        const key = (r.url || r.title || "").toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      updateAssistant((message) => ({
        ...message,
        references: deduped,
      }));
    }
    return;
  }

  if (data.type === "final") {
    const contentLen = (data.content || "").length;
    const artifactCount = (data.artifacts || []).length;
    log.info(`[${eventType}] content_len=${contentLen} artifacts=${artifactCount}`);
    const artifacts = (data.artifacts || []) as GeneratedArtifact[];
    updateAssistant((message) => {
      let updated = setAssistantFinalContent(finalizeThoughtBlocks(message), data.content || "");
      for (const artifact of artifacts) {
        updated = attachArtifact(updated, artifact);
      }
      return updated;
    });
    return;
  }

  if (data.type === "final_override") {
    log.info(`[${eventType}] len=${(data.content || "").length}`);
    updateAssistant((message) => setAssistantFinalContent(finalizeThoughtBlocks(message), data.content || ""));
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

  if (data.type === "stream_end") {
    log.info(`[${eventType}] stream complete`);
    updateAssistant((message) => finalizeThoughtBlocks(message));
    return;
  }

  if (data.content) {
    const preview = (data.content || "").slice(0, 80);
    log.debug(`[content] type="${data.type || "(none)"}" len=${(data.content || "").length} preview="${preview}…"`);
    updateAssistant((message) => appendAnswerBlock(message, data.content || "", false));
    return;
  }

  log.warn(`[unknown] type="${eventType}" no content, ignored`, data);
}