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
import { uuid } from "@/lib/utils";
import type { ChatMessage, GeneratedArtifact, PlanStepSubtask, ReferenceLink, ToolActivity, WorkflowPlan } from "@/types/app";

const log = createLogger("Stream");

function normalizeWorkflowStatus(raw: string): "pending" | "running" | "completed" | "error" {
  if (raw === "completed" || raw === "running" || raw === "pending" || raw === "error") return raw;
  if (raw === "done") return "completed";
  return "pending";
}

// 子任务状态枚举与步骤不同：用 in_progress（非 running），需独立 normalizer。
function normalizeSubtaskStatus(raw: string): PlanStepSubtask["status"] {
  if (raw === "pending" || raw === "in_progress" || raw === "completed" || raw === "cancelled") return raw;
  if (raw === "running") return "in_progress"; // 防御性归一
  if (raw === "done") return "completed";
  return "pending";
}

function mapSubtasks(raw: any[] | undefined): PlanStepSubtask[] {
  return (raw || []).map((s) => ({
    id: String(s?.id ?? ""),
    content: String(s?.content ?? ""),
    status: normalizeSubtaskStatus(String(s?.status ?? "")),
    priority: ["high", "normal", "low"].includes(s?.priority) ? s.priority : "normal",
  }));
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

export interface StreamHandlers {
  updateAssistant: (updater: (message: ChatMessage) => ChatMessage) => void;
  pushToolActivity: (toolName: string, content: string, status: ToolActivity["status"]) => void;
  setWorkflow: (updater: WorkflowPlan | ((prev: WorkflowPlan | null) => WorkflowPlan | null)) => void;
  setIsContextCompressing?: (compressing: boolean) => void;
  setTokenUsage?: (usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number }) => void;
}

export function applyStreamEvent(data: Record<string, any>, handlers: StreamHandlers) {
  const { updateAssistant, pushToolActivity, setWorkflow, setIsContextCompressing, setTokenUsage } = handlers;
  const eventType = data.type || "(no type)";

  if (data.type === "error") {
    const errorMsg = data.content || "处理请求时出错";
    log.info(`[error] content="${errorMsg}"`);
    updateAssistant((message) => ({
      ...message,
      blocks: [
        ...finalizeThoughtBlocks(message).blocks,
        {
          id: uuid(),
          type: "error" as const,
          content: errorMsg,
        },
      ],
    }));
    return;
  }

  if (data.type === "llm_token_error") {
    const errorMsg = data.content || "LLM模型服务账号Token余额不足";
    log.info(`[llm_token_error] content="${errorMsg}"`);
    updateAssistant((message) => ({
      ...message,
      blocks: [
        ...finalizeThoughtBlocks(message).blocks,
        {
          id: uuid(),
          type: "error" as const,
          content: errorMsg,
        },
      ],
    }));
    return;
  }

  if (data.type === "heartbeat") {
    return;
  }

  // ── Phase 1-2 新增事件类型 ──

  if (data.type === "llm_step_start") {
    log.info(`[llm_step_start] iteration=${data.iteration} model=${data.model || ""}`);
    pushToolActivity("llm", data.model ? `${data.model} (第${(data.iteration ?? 0) + 1}轮)` : `Step ${data.iteration ?? ""}`, "running");
    return;
  }

  if (data.type === "llm_step_end") {
    log.info(`[llm_step_end] reason=${data.finish_reason}`);
    const reason = data.finish_reason === "tool_calls" ? "调用工具" : "完成";
    pushToolActivity("llm", reason, "done");
    if (data.tokens && setTokenUsage) {
      setTokenUsage(data.tokens as { prompt_tokens: number; completion_tokens: number; total_tokens: number });
    }
    return;
  }

  if (data.type === "retry_attempt") {
    log.info(`[retry_attempt] attempt=${data.attempt}`);
    pushToolActivity("system", `重试第 ${data.attempt} 次...`, "running");
    return;
  }

  if (data.type === "context_compress_start") {
    log.info(`[context_compress_start]`);
    setIsContextCompressing?.(true);
    updateAssistant((message) => appendThoughtBlock(message, data.content || "正在压缩历史对话...", false));
    return;
  }

  if (data.type === "context_compress_done") {
    log.info(`[context_compress_done] len=${(data.content || "").length}`);
    setIsContextCompressing?.(false);
    updateAssistant((message) => appendActionBlock(message, "context_compress", "done", data.content || "", undefined, "context_compress"));
    return;
  }

  // 思考过程增量。后端 web_server 已将 reasoning/thought_delta 统一归一为 thought_delta，
  // 前端只认这一种思考事件——避免"多套思考逻辑"（reasoning 累加 / thought_summary 归档重建）。
  // Sub-agent thoughts (with step_key) should not leak into main display
  if (data.type === "thought_delta") {
    if (data.step_key) return;
    log.debug(`[${eventType}] len=${(data.content || "").length}`);
    updateAssistant((message) => appendThoughtBlock(message, data.content || "", true));
    return;
  }

  if (data.type === "answer_delta" || data.type === "token") {
    // Sub-agent answer deltas (with step_key) should not leak into main display
    if (data.step_key) return;
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
        subtasks: mapSubtasks(step.subtasks),
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
        log.info(`[${eventType}] unknown step_key="${stepKey}", adding dynamically`);
        steps.push({
          key: stepKey,
          label: data.title || stepKey,
          title: data.title || "",
          status: normalizedStatus,
          detail: data.detail || "",
          outcome: data.outcome || "",
          expected_deliverables: [],
          output_artifacts: [],
          subtasks: mapSubtasks(data.subtasks),
        });
      } else {
        steps[idx] = {
          ...steps[idx],
          status: normalizedStatus,
          ...(data.title ? { title: data.title, label: data.title } : {}),
          ...(data.outcome ? { outcome: data.outcome } : {}),
          // 仅当事件真带 subtasks 时覆盖；后端缺省不下发，避免清空既有子任务
          ...(data.subtasks ? { subtasks: mapSubtasks(data.subtasks) } : {}),
        };
      }
      return { title: prev?.title || "调度计划", steps };
    });
    return;
  }

if (data.type === "action_start" || data.type === "tool_status") {
    const status = data.status === "error" ? "error" : "running";
    const toolName = data.tool_name || "tool";
    const callId = data.call_id || "";
    const stepKey = data.step_key || "";
    const isSubAgent = toolName === "SubAgent" || toolName === "ParallelSubAgent" || toolName === "ParallelTask";
    log.info(`[${eventType}] tool="${toolName}" call_id="${callId}" status="${status}" step_key="${stepKey}"`);
    pushToolActivity(toolName, isSubAgent ? "" : (data.content || ""), status);
    // For SubAgent, use a simplified delegation without detailed task description
    const delegation = isSubAgent
      ? { task: "", label: "SubAgent", skill_name: "" }
      : data.delegation || undefined;
    updateAssistant((message) => appendActionBlock(message, toolName, status, isSubAgent ? "" : (data.content || ""), delegation, callId, undefined, undefined, undefined, stepKey));
    return;
  }

  if (data.type === "permission_ask") {
    const askId = data.ask_id || "";
    const toolName = data.tool_name || "tool";
    const askReason = data.reason || "";
    const askSessionId = data.session_id || "";
    const callId = data.call_id || (askId ? `ask-${askId}` : "");
    const toolInput = (data.tool_input as Record<string, unknown>) ?? undefined;
    log.info(`[${eventType}] permission_ask ask_id="${askId}" tool="${toolName}" call_id="${callId}" reason="${askReason}" has_tool_input=${!!toolInput}`);
    pushToolActivity(toolName, askReason, "pending_confirmation");
    if (callId) {
      updateAssistant((message) =>
        updateActionBlockStatus(message, callId, "pending_confirmation", askReason, { askId, askReason, sessionId: askSessionId, toolInput })
      );
    } else {
      updateAssistant((message) =>
        appendActionBlock(message, toolName, "pending_confirmation", askReason, undefined, "", askId, askReason, askSessionId, undefined, toolInput)
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
    const toolName = data.tool_name || "tool";
    const callId = data.call_id || "";
    const stepKey = data.step_key || "";
    const rawContent = data.content || "";
    const isSubAgent = toolName === "SubAgent" || toolName === "ParallelSubAgent" || toolName === "ParallelTask";
    // SubAgent only shows the tool name label, no internal content
    const displayContent = isSubAgent ? "" : rawContent;
    const contentPreview = displayContent.slice(0, 120);
    log.info(`[${eventType}] tool="${toolName}" call_id="${callId}" step_key="${stepKey}" content=${contentPreview.length > 0 ? `"${contentPreview}…"` : "(empty)"}`);
    pushToolActivity(toolName, displayContent, "done");
    // For SubAgent, use a simplified delegation label without summary
    const delegation = isSubAgent
      ? { task: "", label: "SubAgent", skill_name: "" }
      : data.delegation || undefined;
    updateAssistant((message) => appendActionBlock(message, toolName, "done", displayContent, delegation, callId, undefined, undefined, undefined, stepKey));

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

  if (data.type === "token_usage" && setTokenUsage) {
    setTokenUsage({
      prompt_tokens: data.prompt_tokens || 0,
      completion_tokens: data.completion_tokens || 0,
      total_tokens: data.total_tokens || 0,
    });
    return;
  }

  if (data.content) {
    // Sub-agent content fallthrough (with step_key) should not leak into main display
    if (data.step_key) return;
    const preview = (data.content || "").slice(0, 80);
    log.debug(`[content] type="${data.type || "(none)"}" len=${(data.content || "").length} preview="${preview}…"`);
    updateAssistant((message) => appendAnswerBlock(message, data.content || "", false));
    return;
  }

  log.warn(`[unknown] type="${eventType}" no content, ignored`, data);
}