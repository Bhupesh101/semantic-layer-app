export class ApiError extends Error {
  status: number;
  detail: any;
  constructor(status: number, detail: any) {
    super(typeof detail === "string" ? detail : detail?.message || `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(method: string, path: string, body?: unknown, raw?: string): Promise<T> {
  const res = await fetch(path, {
    method,
    headers:
      raw !== undefined
        ? { "Content-Type": "text/plain" }
        : body
          ? { "Content-Type": "application/json" }
          : {},
    body: raw !== undefined ? raw : body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    const detail = data?.detail ?? data ?? text;
    throw new ApiError(res.status, Array.isArray(detail) ? formatValidation(detail) : detail);
  }
  return data as T;
}

function safeJson(text: string) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function formatValidation(errors: any[]): string {
  return errors.map((e) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  postText: <T>(p: string, text: string) => request<T>("POST", p, undefined, text),
  put: <T>(p: string, b: unknown) => request<T>("PUT", p, b),
  patch: <T>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};

/** Upload with progress (fetch has no upload progress). */
export function uploadFile<T>(path: string, file: File, onProgress: (pct: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      const data = safeJson(xhr.responseText);
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
      else reject(new ApiError(xhr.status, data?.detail ?? data));
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));
    const fd = new FormData();
    fd.append("file", file);
    xhr.send(fd);
  });
}

export interface SSEEvent {
  type: string;
  [k: string]: any;
}

/** POST and read a text/event-stream response. Abort with the signal to stop generation. */
export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (e: SSEEvent) => void,
  signal: AbortSignal,
) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text();
    throw new ApiError(res.status, safeJson(text)?.detail ?? text);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const data = frame
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("\n");
      if (data) onEvent(JSON.parse(data));
    }
  }
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.detail?.issues) return `${e.detail.message} (${e.detail.issues.length} issue(s))`;
    if (e.detail?.in_use) return `${e.detail.message}: ${e.detail.in_use.join(", ")}`;
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function formatNumber(v: any): string {
  if (typeof v === "number") {
    return Number.isInteger(v)
      ? v.toLocaleString()
      : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return v === null || v === undefined ? "—" : String(v);
}

export function timeAgo(iso: string): string {
  const s =
    (Date.now() - new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
