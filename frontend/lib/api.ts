export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
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

/** JSON fetch against the same-origin /api proxy. Throws ApiError on non-2xx. */
export async function api<T>(path: string, init: { method?: string; json?: unknown } = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: init.method ?? (init.json === undefined ? "GET" : "POST"),
    headers: init.json === undefined ? undefined : { "Content-Type": "application/json" },
    body: init.json === undefined ? undefined : JSON.stringify(init.json),
    credentials: "same-origin",
  });
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailText(body?.detail));
  return body as T;
}
