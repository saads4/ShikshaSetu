const BASE = (import.meta.env.VITE_API_URL as string) || "http://localhost:8000/api/v1";

export class ApiProblem extends Error {
  status: number; code: string; title: string; detail: string; errors: { field: string; code: string; message: string; fix_suggestion?: string }[];
  request_id: string; retryable: boolean; body: any;
  constructor(status: number, body: any) {
    super(body?.title || `HTTP ${status}`);
    this.status = status; this.body = body || {}; this.code = body?.code || "ERROR"; this.title = body?.title || "Request failed";
    this.detail = body?.detail || ""; this.errors = body?.errors || []; this.request_id = body?.request_id || ""; this.retryable = !!body?.retryable;
  }
}

let token: string | null = sessionStorage.getItem("ss_token");
export const setToken = (t: string | null) => { token = t; try { t ? sessionStorage.setItem("ss_token", t) : sessionStorage.removeItem("ss_token"); } catch { /* storage may be blocked */ } };
export const getToken = () => token;
let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn; };

export async function api<T = any>(path: string, opts: { method?: string; body?: any; headers?: Record<string, string>; actAs?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.actAs) headers["X-Act-As-Student"] = opts.actAs;
  let res: Response;
  try {
    res = await fetch(BASE + path, { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  } catch {
    throw new ApiProblem(0, { title: "The API is not reachable", detail: "Check that the backend is running on port 8000.", code: "NETWORK", retryable: true });
  }
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { title: text.slice(0, 120) }; }
  if (!res.ok) {
    if (res.status === 401 && data?.code === "SESSION_EXPIRED") onUnauthorized();
    throw new ApiProblem(res.status, data);
  }
  return data as T;
}

/** Authenticated file download (PDF certificates, exports): fetch with the bearer token, then save the blob. */
export async function download(path: string, filename: string, actAs?: string): Promise<void> {
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (actAs) headers["X-Act-As-Student"] = actAs;
  const res = await fetch(BASE + path, { headers });
  if (!res.ok) { let b: any = null; try { b = await res.json(); } catch { /* not json */ } throw new ApiProblem(res.status, b); }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a"); a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** For claims: business outcomes (409/422) carry a body the UI wants to render, so return them rather than throw. */
export async function apiRaw(path: string, body: any, headers: Record<string, string> = {}): Promise<{ status: number; data: any }> {
  const res = await fetch(BASE + path, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...headers }, body: JSON.stringify(body) });
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = {}; }
  if (res.status === 401 && data?.code === "SESSION_EXPIRED") onUnauthorized();
  return { status: res.status, data };
}

/* ---- verify-api: a separate service on its own port; verifier users hold a token for that audience ---- */
export const VERIFY_BASE = (import.meta.env.VITE_VERIFY_URL as string) || "http://localhost:8001";
let vtoken: string | null = null;
try { vtoken = sessionStorage.getItem("ss_vtoken"); } catch { /* storage may be blocked */ }
export const setVerifyToken = (t: string | null) => { vtoken = t; try { t ? sessionStorage.setItem("ss_vtoken", t) : sessionStorage.removeItem("ss_vtoken"); } catch { /* ignore */ } };
export async function vapi<T = any>(path: string, opts: { method?: string; body?: any; auth?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.auth && vtoken) headers["Authorization"] = `Bearer ${vtoken}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try { res = await fetch(VERIFY_BASE + path, { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined }); }
  catch { throw new ApiProblem(0, { title: "The verification service is not reachable", detail: `Check that verify-api is running on ${VERIFY_BASE}.`, code: "NETWORK", retryable: true }); }
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { title: text.slice(0, 120) }; }
  if (!res.ok) throw new ApiProblem(res.status, data);
  return data as T;
}

export const uuid = () => (crypto as any).randomUUID().replace(/-/g, "");
export const fmtDate = (s?: string | null) => (s ? new Date(s).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "-");
export const fmtDateTime = (s?: string | null) => (s ? new Date(s).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "-");
export const daysLeft = (s?: string | null) => (s ? Math.ceil((new Date(s).getTime() - Date.now()) / 86400000) : null);
