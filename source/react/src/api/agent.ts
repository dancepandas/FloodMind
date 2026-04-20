import { apiFetch, buildApiUrl } from "@/api/client";
import { createLogger } from "@/lib/logger";
import type {
  FilePreview,
  SessionConfig,
  SessionSummary,
  StreamSnapshot,
  UploadedFileItem,
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
  model_name: string;
  enable_search: boolean;
  enable_rag: boolean;
  enable_reasoning: boolean;
}

interface ConfigResponse {
  status: string;
  config: SessionConfig;
}

interface SessionStatusResponse {
  status: string;
  in_progress?: StreamSnapshot | null;
  session_state?: Record<string, unknown>;
}

export async function initAgent(sessionId: string, config: SessionConfig): Promise<InitResponse> {
  log.info("initAgent", { sessionId, config });
  const result = await apiFetch<InitResponse>("/api/init", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, ...config }),
  });
  log.info("initAgent →", result.model_name, { search: result.enable_search, rag: result.enable_rag, reasoning: result.enable_reasoning });
  return result;
}

export async function updateSessionConfig(sessionId: string, config: SessionConfig): Promise<ConfigResponse> {
  log.info("updateSessionConfig", { sessionId, config });
  const result = await apiFetch<ConfigResponse>("/api/session/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, ...config }),
  });
  log.info("updateSessionConfig → OK");
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

export async function uploadFile(sessionId: string, file: File): Promise<void> {
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
  log.info("uploadFile → OK", file.name);
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

export async function resumeSession(sessionId: string): Promise<void> {
  log.info("resumeSession", sessionId);
  await apiFetch<{ status: string }>("/api/session/resume", {
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

export function downloadLogsZip(): void {
  log.info("downloadLogsZip");
  window.open(buildApiUrl("/api/logs"), "_blank");
}

export function downloadSessionOutputs(sessionId: string): void {
  log.info("downloadSessionOutputs", sessionId);
  window.open(buildApiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/outputs/download`), "_blank");
}
