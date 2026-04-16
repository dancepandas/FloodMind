const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

export function buildApiUrl(path: string): string {
  if (!API_BASE) return path;
  return `${API_BASE}${path}`;
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildApiUrl(path), init);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}
