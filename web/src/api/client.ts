import { createLogger } from "@/lib/logger";

const log = createLogger("API");

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

export function buildApiUrl(path: string): string {
  if (!API_BASE) return path;
  return `${API_BASE}${path}`;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = buildApiUrl(path);
  const method = init?.method || "GET";
  log.info(`${method} ${path}`);
  const startTime = performance.now();

  try {
    const response = await fetch(url, init);
    const elapsed = Math.round(performance.now() - startTime);

    if (!response.ok) {
      const text = await response.text();
      log.error(`${method} ${path} → ${response.status} (${elapsed}ms)`, text || "(empty body)");
      throw new Error(text || `Request failed: ${response.status}`);
    }

    log.info(`${method} ${path} → ${response.status} OK (${elapsed}ms)`);
    return response.json() as Promise<T>;
  } catch (err) {
    const elapsed = Math.round(performance.now() - startTime);
    log.error(`${method} ${path} → FAILED (${elapsed}ms)`, err);
    throw err;
  }
}
