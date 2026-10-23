const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
let csrfToken = "";

type ApiError = Error & { status?: number; code?: string };

async function readError(response: Response): Promise<ApiError> {
  const payload = await response.json().catch(() => ({}));
  const error = new Error(payload?.error?.message ?? `Request failed (${response.status}).`) as ApiError;
  error.status = response.status;
  error.code = payload?.error?.code;
  return error;
}

export async function refreshCsrf(): Promise<void> {
  const response = await fetch(`${API_BASE}/api/auth/csrf`, { credentials: "include" });
  if (!response.ok) throw await readError(response);
  const payload = await response.json();
  csrfToken = payload.csrf_token;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && !csrfToken) await refreshCsrf();
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(`${API_BASE}/api${path}`, {
    ...options,
    method,
    headers,
    credentials: "include",
  });
  if (!response.ok) throw await readError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function download(path: string, filename: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api${path}`, { credentials: "include" });
  if (!response.ok) throw await readError(response);
  const blob = await response.blob();
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}
