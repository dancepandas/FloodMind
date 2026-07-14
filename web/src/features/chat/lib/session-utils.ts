import type { GeneratedArtifact } from "@/types/app";

/** localStorage 中持久化当前 sessionId 的键。 */
export const SESSION_STORAGE_KEY = "floodmind_react_session_id";

/** 生成一个形如 session-<timestamp>-<rand> 的会话 id。 */
export function generateSessionId(): string {
  return `session-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 把后端返回的 artifact 原始对象归一化为前端 GeneratedArtifact。
 * 仅接受 file_generated / image_generated 且带 filename 的项，其余返回 null。
 */
export function normalizeArtifact(raw: Record<string, unknown>): GeneratedArtifact | null {
  const type = raw.type;
  const filename = raw.filename;
  if ((type !== "file_generated" && type !== "image_generated") || typeof filename !== "string") {
    return null;
  }

  return {
    type,
    filename,
    filepath: typeof raw.filepath === "string" ? raw.filepath : undefined,
    size: typeof raw.size === "number" ? raw.size : undefined,
    download_url: typeof raw.download_url === "string" ? raw.download_url : undefined,
    image_url: typeof raw.image_url === "string" ? raw.image_url : undefined,
    image_data: typeof raw.image_data === "string" ? raw.image_data : undefined,
  };
}
