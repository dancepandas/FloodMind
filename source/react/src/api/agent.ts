import { apiFetch, buildApiUrl } from "@/api/client";
import type {
  FilePreview,
  SessionConfig,
  SessionSummary,
  StreamSnapshot,
  UploadedFileItem,
} from "@/types/app";

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
  return apiFetch<InitResponse>("/api/init", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, ...config }),
  });
}

export async function updateSessionConfig(sessionId: string, config: SessionConfig): Promise<ConfigResponse> {
  return apiFetch<ConfigResponse>("/api/session/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, ...config }),
  });
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  const data = await apiFetch<SessionsResponse>("/api/sessions");
  return data.sessions || [];
}

export async function fetchSession(sessionId: string): Promise<SessionDetailResponse> {
  return apiFetch<SessionDetailResponse>(`/api/sessions/${encodeURIComponent(sessionId)}`);
}

export async function fetchSessionFiles(sessionId: string): Promise<UploadedFileItem[]> {
  const data = await apiFetch<FilesResponse>(`/api/files?session_id=${encodeURIComponent(sessionId)}`);
  return data.files || [];
}

export async function fetchFilePreview(sessionId: string, fileId: string): Promise<FilePreview> {
  const data = await apiFetch<FilePreviewResponse>(`/api/files/${encodeURIComponent(fileId)}/preview?session_id=${encodeURIComponent(sessionId)}`);
  return data.preview;
}

export async function uploadFile(sessionId: string, file: File): Promise<void> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("session_id", sessionId);
  const response = await fetch(buildApiUrl("/api/upload"), {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || "Upload failed");
  }
}

export async function fetchSessionStatus(sessionId: string): Promise<SessionStatusResponse> {
  return apiFetch<SessionStatusResponse>(`/api/session/status?session_id=${encodeURIComponent(sessionId)}`);
}

export async function pauseSession(sessionId: string): Promise<void> {
  await apiFetch<{ status: string }>("/api/session/pause", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function resumeSession(sessionId: string): Promise<void> {
  await apiFetch<{ status: string }>("/api/session/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function saveSession(sessionId: string): Promise<void> {
  await apiFetch<{ status: string }>("/api/sessions/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function deleteSession(sessionId: string): Promise<void> {
  await apiFetch<{ status: string }>(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}

export function createChatRequest(sessionId: string, message: string, uploadedFiles: string[], assistantMessageId: string) {
  return fetch(buildApiUrl("/api/chat"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      message,
      uploaded_files: uploadedFiles,
      assistant_message_id: assistantMessageId,
    }),
  });
}
