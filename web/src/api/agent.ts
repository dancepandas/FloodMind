import { apiFetch, buildApiUrl } from "@/api/client";
import { createLogger } from "@/lib/logger";
import type {
  FilePreview,
  SessionConfig,
  SessionSummary,
  ScheduledTask,
  StreamSnapshot,
  UploadedFileItem,
  ModelsResponse,
  CheckpointSummary,
  CheckpointManifest,
  TraceItem,
} from "@/types/app";

const log = createLogger("Agent");

interface SessionsResponse {
  status: string;
  sessions: Array<SessionSummary>;
}

interface SessionDetailResponse {
  status: string;
  session: SessionSummary;
  messages: Array<Record<string, unknown>>;
  artifacts: Array<Record<string, unknown>>;
  in_progress?: StreamSnapshot | null;
}

interface FilesResponse {
  status: string;
  files: UploadedFileItem[];
}

interface FilePreviewResponse {
  status: string;
  preview: FilePreview;
}

interface InitResponse {
  status: string;
  model_key: string;
  model_name: string;
  enable_search: boolean;
  enable_reasoning: boolean;
}

interface ConfigResponse {
  status: string;
  config: SessionConfig & { model_name?: string };
}

interface SessionStatusResponse {
  status: string;
  in_progress?: StreamSnapshot | null;
  session_state?: Record<string, unknown>;
}

interface ScheduledTasksResponse {
  status: string;
  count: number;
  tasks: ScheduledTask[];
}

export async function initAgent(sessionId: string, config: SessionConfig): Promise<InitResponse> {
  log.info("initAgent", { sessionId, config });
  const result = await apiFetch<InitResponse>("/api/init", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, ...config }),
  });
  log.info("initAgent →", result.model_key, result.model_name, { search: result.enable_search, reasoning: result.enable_reasoning });
  return result;
}

export async function updateSessionConfig(sessionId: string, config: Partial<SessionConfig>): Promise<ConfigResponse> {
  log.info("updateSessionConfig", { sessionId, config });
  const result = await apiFetch<ConfigResponse>("/api/session/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, ...config }),
  });
  log.info("updateSessionConfig → OK");
  return result;
}

export async function fetchModels(): Promise<ModelsResponse> {
  log.info("fetchModels");
  const result = await apiFetch<ModelsResponse>("/api/models");
  log.info("fetchModels →", result.models?.length || 0, "models, default:", result.default_model_key);
  return result;
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  log.debug("fetchSessions");
  const data = await apiFetch<SessionsResponse>("/api/sessions");
  log.info("fetchSessions →", data.sessions?.length || 0, "sessions");
  return data.sessions || [];
}

export async function fetchSession(sessionId: string): Promise<SessionDetailResponse> {
  log.info("fetchSession", sessionId);
  const result = await apiFetch<SessionDetailResponse>(`/api/sessions/${encodeURIComponent(sessionId)}`);
  log.info("fetchSession →", result.messages?.length || 0, "messages,", result.artifacts?.length || 0, "artifacts");
  return result;
}

export async function fetchSessionFiles(sessionId: string): Promise<UploadedFileItem[]> {
  log.debug("fetchSessionFiles", sessionId);
  const data = await apiFetch<FilesResponse>(`/api/files?session_id=${encodeURIComponent(sessionId)}`);
  log.info("fetchSessionFiles →", data.files?.length || 0, "files");
  return data.files || [];
}

export async function fetchFilePreview(sessionId: string, fileId: string): Promise<FilePreview> {
  log.info("fetchFilePreview", { sessionId, fileId });
  const data = await apiFetch<FilePreviewResponse>(`/api/files/${encodeURIComponent(fileId)}/preview?session_id=${encodeURIComponent(sessionId)}`);
  return data.preview;
}

export async function uploadFile(sessionId: string, file: File): Promise<UploadedFileItem> {
  log.info("uploadFile", { sessionId, filename: file.name, size: file.size });
  const formData = new FormData();
  formData.append("file", file);
  formData.append("session_id", sessionId);
  const response = await fetch(buildApiUrl("/api/upload"), {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const text = await response.text();
    log.error("uploadFile → FAILED", response.status, text);
    throw new Error(text || "Upload failed");
  }
  const data = await response.json();
  const item: UploadedFileItem = {
    id: data.file_id,
    name: data.file_name || file.name,
    size: typeof data.size === "number" ? data.size : file.size,
  };
  log.info("uploadFile → OK", file.name, item.id);
  return item;
}

export async function fetchSessionStatus(sessionId: string): Promise<SessionStatusResponse> {
  log.debug("fetchSessionStatus", sessionId);
  const result = await apiFetch<SessionStatusResponse>(`/api/session/status?session_id=${encodeURIComponent(sessionId)}`);
  log.info("fetchSessionStatus →", result.in_progress ? "has in_progress" : "idle", result.session_state ? { session_state: result.session_state } : "");
  return result;
}

export async function pauseSession(sessionId: string): Promise<void> {
  log.info("pauseSession", sessionId);
  await apiFetch<{ status: string }>("/api/session/pause", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function saveSession(sessionId: string): Promise<void> {
  log.info("saveSession", sessionId);
  await apiFetch<{ status: string }>("/api/sessions/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function deleteSession(sessionId: string): Promise<void> {
  log.info("deleteSession", sessionId);
  await apiFetch<{ status: string }>(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}

export async function fetchScheduledTasks(sessionId?: string): Promise<ScheduledTask[]> {
  log.debug("fetchScheduledTasks", sessionId || "all");
  const params = sessionId ? `session_id=${encodeURIComponent(sessionId)}` : "include_all=1";
  const data = await apiFetch<ScheduledTasksResponse>(`/api/scheduled-tasks?${params}`);
  log.info("fetchScheduledTasks →", data.tasks?.length || 0, "tasks");
  return data.tasks || [];
}

export async function deleteScheduledTask(taskId: string): Promise<void> {
  log.info("deleteScheduledTask", taskId);
  await apiFetch<{ status: string }>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
}

export function createChatRequest(sessionId: string, message: string, uploadedFiles: string[], assistantMessageId: string) {
  log.info("createChatRequest", { sessionId, message: message.slice(0, 80), fileCount: uploadedFiles.length, assistantMessageId });
  const startTime = performance.now();
  const response = fetch(buildApiUrl("/api/chat"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      message,
      uploaded_files: uploadedFiles,
      assistant_message_id: assistantMessageId,
    }),
  });
  response.then((res) => {
    const elapsed = Math.round(performance.now() - startTime);
    log.info(`createChatRequest → ${res.status} (${elapsed}ms)`, res.ok ? "OK" : "FAILED");
  }).catch((err) => {
    const elapsed = Math.round(performance.now() - startTime);
    log.error(`createChatRequest → ERROR (${elapsed}ms)`, err);
  });
  return response;
}

export function resumeStreamRequest(sessionId: string, afterIndex = 0): Promise<Response> {
  log.info("resumeStreamRequest", { sessionId, afterIndex });
  return fetch(buildApiUrl(`/api/stream/resume?session_id=${encodeURIComponent(sessionId)}&after_index=${afterIndex}`));
}

export async function respondPermissionAsk(askId: string, approved: boolean, sessionId: string): Promise<{ status: string; ask_id?: string; approved?: boolean; message?: string }> {
  log.info("respondPermissionAsk", { askId, approved, sessionId });
  const result = await apiFetch<{ status: string; ask_id?: string; approved?: boolean; message?: string }>("/api/permission/respond", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ask_id: askId, approved, session_id: sessionId }),
  });
  return result;
}

export function downloadLogsZip(): void {
  log.info("downloadLogsZip");
  window.open(buildApiUrl("/api/logs"), "_blank");
}

export function downloadSessionOutputs(sessionId: string): void {
  log.info("downloadSessionOutputs", sessionId);
  window.open(buildApiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/outputs/download`), "_blank");
}

// ── Checkpoint API ───────────────────────────────────────────────

interface CheckpointsResponse {
  status: string;
  checkpoints: CheckpointSummary[];
}

interface CheckpointManifestResponse {
  status: string;
  manifest: CheckpointManifest;
}

interface CheckpointRollbackResponse {
  status: string;
  checkpoint_id: string;
  restored_files: string[];
}

export async function fetchCheckpoints(sessionId: string): Promise<CheckpointSummary[]> {
  log.info("fetchCheckpoints", sessionId);
  const data = await apiFetch<CheckpointsResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/checkpoints`);
  return data.checkpoints || [];
}

export async function fetchCheckpointManifest(sessionId: string, checkpointId: string): Promise<CheckpointManifest> {
  log.info("fetchCheckpointManifest", { sessionId, checkpointId });
  const data = await apiFetch<CheckpointManifestResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/checkpoints/${encodeURIComponent(checkpointId)}`);
  return data.manifest;
}

export async function rollbackCheckpoint(sessionId: string, checkpointId: string): Promise<CheckpointRollbackResponse> {
  log.info("rollbackCheckpoint", { sessionId, checkpointId });
  const data = await apiFetch<CheckpointRollbackResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/checkpoints/${encodeURIComponent(checkpointId)}/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return data;
}

// ── Tracing API ──────────────────────────────────────────────────

interface TraceEventsResponse {
  status: string;
  session_id: string;
  events: TraceItem[];
}

export async function fetchTraceEvents(sessionId: string, limit = 200): Promise<TraceItem[]> {
  log.info("fetchTraceEvents", { sessionId, limit });
  const data = await apiFetch<TraceEventsResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/traces?limit=${limit}`);
  return data.events || [];
}

export function downloadTraceFile(sessionId: string): void {
  log.info("downloadTraceFile", sessionId);
  window.open(buildApiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/traces/download`), "_blank");
}

