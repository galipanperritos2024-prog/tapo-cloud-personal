/**
 * Tiny API client.
 *
 * - Same-origin requests with the session cookie (HttpOnly, set by the backend).
 * - Every mutating request carries the CSRF token read from the `tc_csrf` cookie.
 * - A 401 sends the user to the login page.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)tc_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      // Pydantic validation errors: show the first one in a readable way.
      const d = body.detail[0];
      return `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`;
    }
  } catch {
    /* not JSON */
  }
  return `Error ${res.status}`;
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const method = (rest.method ?? (json !== undefined ? "POST" : "GET")).toUpperCase();
  const h = new Headers(headers);
  if (json !== undefined) h.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) h.set("X-CSRF-Token", csrfToken());

  const res = await fetch(path, {
    ...rest,
    method,
    headers: h,
    credentials: "same-origin",
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login/";
    }
    throw new ApiError(401, "No autenticado");
  }
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.blob()) as Promise<T>;
}

/** SWR fetcher. */
export const fetcher = <T,>(path: string) => api<T>(path);
