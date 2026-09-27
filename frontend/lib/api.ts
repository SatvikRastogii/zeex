export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail: unknown = null,
  ) {
    super(message);
  }
}

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = (d.loc ?? []).filter((x) => x !== "body").join(".");
        return field ? `${field}: ${d.msg}` : String(d.msg);
      })
      .join("; ");
  }
  return "Request failed";
}

type Init = { method?: string; json?: unknown; body?: BodyInit };

/** JSON fetch against the same-origin /api proxy. Throws ApiError on non-2xx. */
export async function api<T>(path: string, init: Init = {}): Promise<T> {
  const hasJson = init.json !== undefined;
  const res = await fetch(`/api${path}`, {
    method: init.method ?? (hasJson || init.body !== undefined ? "POST" : "GET"),
    headers: hasJson ? { "Content-Type": "application/json" } : init.body ? { "Content-Type": "application/octet-stream" } : undefined,
    body: hasJson ? JSON.stringify(init.json) : init.body,
    credentials: "same-origin",
  });
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailText(body?.detail), body?.detail ?? null);
  return body as T;
}
