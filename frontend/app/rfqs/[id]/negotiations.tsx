"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatINR, formatIST, statusTag } from "@/lib/format";
import { useSession } from "@/lib/session";

type Thread = {
  id: string;
  code: string;
  vendor: string;
  state: string;
  round: number;
  opening_offer_paise: number | null;
  current_offer_paise: number | null;
  last_counter_paise: number | null;
  deadline_at: string | null;
  reply_due_at: string | null;
  handoff_reason: string | null;
  taken_over: boolean;
  messages: { id: string; direction: string; body: string; sent_at: string; stale: boolean; by_human: boolean; status: string }[];
};

function ThreadCard({ t, canAct, onChange }: { t: Thread; canAct: boolean; onChange: () => void }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function post(path: string, json?: unknown) {
    setError(null);
    try {
      await api(`/negotiations/${t.id}/${path}`, { method: "POST", json });
      setText("");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    }
  }

  const saved = t.opening_offer_paise && t.current_offer_paise ? t.opening_offer_paise - t.current_offer_paise : 0;
  return (
    <section className="box" style={{ margin: 0, minWidth: 0 }} aria-label={`Negotiation with ${t.vendor}`}>
      <strong>{t.vendor}</strong>
      <div className="small mono">
        {t.code} · <span className="status">{statusTag(t.state)}</span> · round {t.round}
      </div>
      <table className="small" style={{ margin: "6px 0" }}>
        <tbody>
          <tr>
            <th>Opening (landed)</th>
            <td className="num">{t.opening_offer_paise ? formatINR(t.opening_offer_paise) : "-"}</td>
          </tr>
          <tr>
            <th>Current offer</th>
            <td className="num">{t.current_offer_paise ? formatINR(t.current_offer_paise) : "-"}</td>
          </tr>
          <tr>
            <th>Saved per unit</th>
            <td className="num">{formatINR(saved)}</td>
          </tr>
          <tr>
            <th>Our last ask</th>
            <td className="num">{t.last_counter_paise ? formatINR(t.last_counter_paise) : "best and final"}</td>
          </tr>
        </tbody>
      </table>
      {t.handoff_reason && <p className="small">Handed to a person: {t.handoff_reason}</p>}
      {t.reply_due_at && ["awaiting_reply", "counter_sent"].includes(t.state) && (
        <p className="small muted">Reply due {formatIST(t.reply_due_at)}</p>
      )}
      <div style={{ maxHeight: 320, overflowY: "auto", borderTop: "1px solid #ddd", paddingTop: 6 }}>
        {t.messages.map((m) => (
          <div key={m.id} className="small" style={{ margin: "4px 0", textAlign: m.direction === "in" ? "right" : "left" }}>
            <div className="muted">
              {m.direction === "in" ? "Vendor" : m.by_human ? "You" : "Agent"} · {formatIST(m.sent_at)}
              {m.stale && " · [STALE: answered an older round]"}
            </div>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.body}</div>
          </div>
        ))}
      </div>
      {error && <p className="error">{error}</p>}
      {canAct && t.state !== "closed" && !t.taken_over && (
        <p>
          <button type="button" className="secondary" onClick={() => post("take-over")}>
            Take over
          </button>
        </p>
      )}
      {canAct && t.taken_over && t.state !== "closed" && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) void post("message", { text });
          }}
        >
          <label htmlFor={`msg-${t.id}`}>Message the vendor (the agent will not send on this thread)</label>
          <input id={`msg-${t.id}`} value={text} onChange={(e) => setText(e.target.value)} style={{ width: "100%" }} maxLength={1000} />
          <p>
            <button type="submit" disabled={!text.trim()}>
              Send
            </button>{" "}
            <button
              type="button"
              className="secondary"
              onClick={() => window.confirm("Close this negotiation? The vendor's last confirmed offer stands.") && post("close")}
            >
              Close negotiation
            </button>
          </p>
        </form>
      )}
    </section>
  );
}

export default function Negotiations({ rfqId }: { rfqId: string }) {
  const { me } = useSession();
  const canAct = me?.kind === "user" && ["owner", "purchase_manager"].includes(me.role);
  const [threads, setThreads] = useState<Thread[]>([]);

  const load = useCallback(() => {
    api<Thread[]>(`/rfqs/${rfqId}/negotiations`)
      .then(setThreads)
      .catch(() => setThreads([]));
  }, [rfqId]);
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  if (threads.length === 0) return null;
  return (
    <>
      <h2>Negotiation</h2>
      <p className="small muted">
        The agent writes the words; every price is decided by rules (match the best real offer, or 2-3% for the best vendor, never below 92% of
        the reference price). Your target and maximum are never shared. A vendor&apos;s &quot;ok&quot; is their best and final offer, not a deal.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12 }}>
        {threads.map((t) => (
          <ThreadCard key={t.id} t={t} canAct={canAct} onChange={load} />
        ))}
      </div>
    </>
  );
}
