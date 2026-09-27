"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

type DemoState = {
  settings: { auto_reply: boolean; persona_mode: "scripted" | "gemini" };
  llm: string;
  status: { state: string; current?: string | null; error?: string; results?: Record<string, { name: string; status: string }> };
  scenarios: { key: string; name: string }[];
  personas: string[];
};
type VendorPersona = { id: string; name: string; persona: string; items: string[]; opted_out: boolean };

export default function DemoTools({ onChange }: { onChange: () => void }) {
  const [s, setS] = useState<DemoState | null>(null);
  const [vendors, setVendors] = useState<VendorPersona[]>([]);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const load = useCallback(async () => {
    try {
      setS(await api<DemoState>("/admin/demo"));
      setVendors(await api<VendorPersona[]>("/admin/vendors"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) router.push("/login"); // reset signs everyone out
    }
  }, [router]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  useEffect(() => {
    if (s?.status.state !== "running") return;
    const t = setInterval(() => {
      void load();
      onChange();
    }, 2000);
    return () => clearInterval(t);
  }, [s?.status.state, load, onChange]);

  async function start(body: { reset?: boolean; scenarios?: string[]; play?: boolean }, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setError(null);
    try {
      await api("/admin/demo/load", { json: body });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed");
    }
  }

  async function setting(changes: Partial<DemoState["settings"]>) {
    await api("/admin/demo/settings", { method: "PUT", json: changes });
    await load();
  }

  if (!s) return null;
  const running = s.status.state === "running";
  const all = s.scenarios.map((x) => x.key);

  return (
    <>
      <div className="box">
        <h2>Scenarios</h2>
        <p className="small muted">
          Each scenario is played through the real product (vendors quote and reply from their inbox, the clock moves) and stops at the point
          described in docs/DEMO_SCRIPT.md. Loading moves the demo clock forward.
        </p>
        <table>
          <tbody>
            {s.scenarios.map((sc) => (
              <tr key={sc.key}>
                <td className="num">{sc.key}</td>
                <td>{sc.name}</td>
                <td className="status">{s.status.results?.[sc.key] ? `[${s.status.results[sc.key].status.replace(/_/g, " ").toUpperCase()}]` : ""}</td>
                <td>
                  <button type="button" className="secondary" disabled={running} onClick={() => start({ scenarios: [sc.key] })}>
                    Load
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>
          <button type="button" disabled={running} onClick={() => start({ scenarios: all })}>
            Load all scenarios
          </button>{" "}
          <button type="button" className="secondary" disabled={running} onClick={() => start({ play: true })}>
            Run full scenario 1 (to a closed order)
          </button>{" "}
          <button
            type="button"
            className="secondary"
            disabled={running}
            onClick={() => start({ reset: true, scenarios: all }, "Wipe all demo data, re-seed and load every scenario? Everyone is signed out.")}
          >
            Reset and load all
          </button>{" "}
          <button
            type="button"
            className="secondary"
            disabled={running}
            onClick={() => start({ reset: true }, "Wipe all demo data and re-seed? Everyone is signed out.")}
          >
            Reset only
          </button>
        </p>
        <p className="status">
          [{s.status.state.toUpperCase()}] {s.status.current ?? ""} {s.status.error ?? ""}
          {s.status.results?.play && ` Full run: [${s.status.results.play.status.toUpperCase()}]`}
        </p>
        {error && <p className="error">{error}</p>}
      </div>

      <div className="box">
        <h2>Vendor personas</h2>
        <label>
          <input type="checkbox" checked={s.settings.auto_reply} onChange={(e) => setting({ auto_reply: e.target.checked })} /> Vendors reply by
          themselves (10 demo-minutes after each message)
        </label>
        <label htmlFor="persona-mode">Reply text</label>
        <select id="persona-mode" value={s.settings.persona_mode} onChange={(e) => setting({ persona_mode: e.target.value as "scripted" | "gemini" })}>
          <option value="scripted">Scripted (free, repeatable)</option>
          <option value="gemini">Gemini writes in character (uses API credits)</option>
        </select>
        {s.settings.persona_mode === "gemini" && s.llm === "mock" && (
          <p className="small">
            <span className="sim">Simulated</span> No Gemini key is configured, so scripted text is used.
          </p>
        )}
        <table>
          <thead>
            <tr>
              <th>Vendor</th>
              <th>Supplies</th>
              <th>Persona</th>
            </tr>
          </thead>
          <tbody>
            {vendors.map((v) => (
              <tr key={v.id}>
                <td>
                  {v.name}
                  {v.opted_out && <span className="status"> [OPTED OUT]</span>}
                </td>
                <td className="small mono">{v.items.join(", ")}</td>
                <td>
                  <select
                    aria-label={`Persona for ${v.name}`}
                    value={v.persona}
                    onChange={async (e) => {
                      await api(`/admin/vendors/${v.id}/persona`, { method: "PUT", json: { persona: e.target.value } });
                      await load();
                    }}
                  >
                    {s.personas.map((p) => (
                      <option key={p} value={p}>
                        {p.replace(/_/g, " ")}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
