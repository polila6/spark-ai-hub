// Same-origin API client. Session cookie is httpOnly; CSRF token is read from a JS-readable cookie
// and echoed in X-CSRF-Token for unsafe methods (double-submit). The frontend is not a security
// boundary: every permission is enforced by the backend.

export class ApiError extends Error {
  status: number;
  code: string;
  detail: any;
  constructor(status: number, code: string, message: string, detail?: any) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function csrf(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|; )sah_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function parse(res: Response) {
  const ct = res.headers.get("content-type") || "";
  const body = ct.includes("json") ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    const e = (body && (body.error || body.detail)) || {};
    const msg = typeof e === "string" ? e : e.message || res.statusText;
    throw new ApiError(res.status, e.code || String(res.status), msg, e);
  }
  return body;
}

export async function api<T = any>(path: string, opts: RequestInit & { json?: any } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) };
  const method = (opts.method || "GET").toUpperCase();
  if (!["GET", "HEAD"].includes(method)) headers["X-CSRF-Token"] = csrf();
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, { ...opts, method, headers, body, credentials: "same-origin" });
  if (res.status === 401 && typeof window !== "undefined" && !path.includes("/auth/")) {
    const next = encodeURIComponent(window.location.pathname);
    if (!window.location.pathname.startsWith("/login")) window.location.href = `/login?next=${next}`;
  }
  return parse(res) as Promise<T>;
}

export const fetcher = (path: string) => api(path);

/** POST JSON and return the raw response body as a Blob (audio, exported files). */
export async function postBlob(path: string, json: any): Promise<Blob> {
  const res = await fetch(path, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() }, body: JSON.stringify(json) });
  if (!res.ok) await parse(res);
  return res.blob();
}

export async function upload<T = any>(path: string, form: FormData): Promise<T> {
  const res = await fetch(path, { method: "POST", body: form, credentials: "same-origin", headers: { "X-CSRF-Token": csrf() } });
  return parse(res) as Promise<T>;
}

/** POST that returns an SSE stream; invokes onEvent for each JSON `data:` line. */
export async function streamPost(path: string, json: any, onEvent: (ev: any) => void, signal?: AbortSignal) {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf(), Accept: "text/event-stream" },
    body: JSON.stringify(json),
    signal,
  });
  return readSSE(res, onEvent);
}

/** GET an SSE stream (e.g. re-attach to a chat answer that is still running). */
export async function streamGet(path: string, onEvent: (ev: any) => void, signal?: AbortSignal) {
  const res = await fetch(path, { credentials: "same-origin", headers: { Accept: "text/event-stream" }, signal });
  return readSSE(res, onEvent);
}

async function readSSE(res: Response, onEvent: (ev: any) => void) {
  if (!res.ok || !res.body) await parse(res);
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch {
            /* keep-alive or non-JSON */
          }
        }
      }
    }
  }
}
