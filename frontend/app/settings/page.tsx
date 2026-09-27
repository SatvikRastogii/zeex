"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatINR, rupeesToPaise } from "@/lib/format";
import { useSession } from "@/lib/session";
import Guard from "../guard";

type Settings = {
  weights: Record<string, number>;
  gst_mode: "incl" | "excl";
  disclosure: "off" | "lower_offer_only" | "lower_offer_then_price";
  approval_limits_paise: { purchase_manager: number };
  working_hours: { start: string; end: string };
  bid_window_hours: number;
  max_rounds: number;
  shortlist_size: number;
  match_top_n: number;
  po_confirm_working_hours: number;
  reply_timeout_working_hours: number;
};

const WEIGHTS = [
  ["price", "Landed price"],
  ["delivery", "Delivery time"],
  ["payment", "Payment terms"],
  ["reliability", "Vendor reliability"],
  ["quality", "Quality / compliance"],
] as const;

const NUMBERS = [
  ["bid_window_hours", "Bid window (hours)"],
  ["max_rounds", "Negotiation rounds"],
  ["shortlist_size", "Vendors to negotiate with"],
  ["match_top_n", "Vendors to invite"],
  ["po_confirm_working_hours", "Vendor must confirm a PO within (working hours)"],
  ["reply_timeout_working_hours", "Negotiation reply timeout (working hours)"],
] as const;

function Form() {
  const { me } = useSession();
  const isOwner = me?.kind === "user" && me.role === "owner";
  const [s, setS] = useState<Settings | null>(null);
  const [limit, setLimit] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ settings: Settings }>("/org").then((o) => {
      setS(o.settings);
      setLimit(String(o.settings.approval_limits_paise.purchase_manager / 100));
    });
  }, []);

  if (!s) return <p className="muted">Loading...</p>;
  const sum = WEIGHTS.reduce((t, [k]) => t + (Number(s.weights[k]) || 0), 0);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setMsg(null);
    try {
      const body = { ...s, approval_limits_paise: { purchase_manager: rupeesToPaise(limit) ?? 0 } };
      const out = await api<{ settings: Settings }>("/org/settings", { method: "PUT", json: body });
      setS(out.settings);
      setMsg("Saved. New weights apply the next time quotes are scored.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed");
    }
  }

  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS({ ...s, [k]: v });

  return (
    <form onSubmit={save}>
      <h1>Settings</h1>
      {!isOwner && <p className="box">Only the owner can change settings.</p>}
      <fieldset disabled={!isOwner} style={{ border: 0, padding: 0, margin: 0 }}>
        <h2>Evaluation weights (must add up to 100)</h2>
        <table>
          <tbody>
            {WEIGHTS.map(([k, label]) => (
              <tr key={k}>
                <th>
                  <label htmlFor={`w-${k}`}>{label}</label>
                </th>
                <td>
                  <input
                    id={`w-${k}`}
                    inputMode="numeric"
                    size={4}
                    value={s.weights[k]}
                    onChange={(e) => set("weights", { ...s.weights, [k]: Number(e.target.value) || 0 })}
                  />{" "}
                  %
                </td>
              </tr>
            ))}
            <tr>
              <th>Total</th>
              <td className="num">{sum}%{sum !== 100 && " (must be 100)"}</td>
            </tr>
          </tbody>
        </table>
        <h2>Comparison and negotiation</h2>
        <label htmlFor="gst">Compare quotes</label>
        <select id="gst" value={s.gst_mode} onChange={(e) => set("gst_mode", e.target.value as Settings["gst_mode"])}>
          <option value="incl">including GST</option>
          <option value="excl">excluding GST</option>
        </select>
        <label htmlFor="disc">When a vendor asks about competing offers</label>
        <select id="disc" value={s.disclosure} onChange={(e) => set("disclosure", e.target.value as Settings["disclosure"])}>
          <option value="lower_offer_then_price">Say &quot;we have a lower offer&quot;; give the exact price if asked again</option>
          <option value="lower_offer_only">Only say &quot;we have a lower offer&quot;</option>
          <option value="off">Never mention other offers</option>
        </select>
        {NUMBERS.map(([k, label]) => (
          <div key={k}>
            <label htmlFor={k}>{label}</label>
            <input id={k} inputMode="numeric" size={5} value={s[k]} onChange={(e) => set(k, Number(e.target.value) || 0)} />
          </div>
        ))}
        <h2>Approvals and working hours</h2>
        <label htmlFor="limit">Purchase manager approval limit (₹)</label>
        <input id="limit" inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value)} />{" "}
        <span className="small muted">now {formatINR(s.approval_limits_paise.purchase_manager)}; above it, approvals go to the owner</span>
        <label htmlFor="wh-start">Messages to vendors between</label>
        <input id="wh-start" type="time" value={s.working_hours.start} onChange={(e) => set("working_hours", { ...s.working_hours, start: e.target.value })} />{" "}
        and{" "}
        <input
          aria-label="Working hours end"
          type="time"
          value={s.working_hours.end}
          onChange={(e) => set("working_hours", { ...s.working_hours, end: e.target.value })}
        />{" "}
        IST
        {error && <p className="error">{error}</p>}
        {msg && <p className="box">{msg}</p>}
        <p>
          <button type="submit" disabled={sum !== 100}>
            Save settings
          </button>
        </p>
      </fieldset>
    </form>
  );
}

export default function SettingsPage() {
  return (
    <Guard need="builder">
      <Form />
    </Guard>
  );
}
