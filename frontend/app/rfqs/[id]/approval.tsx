"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatINR, newRef, statusTag } from "@/lib/format";
import { useSession } from "@/lib/session";

type Ranked = { vendor_id: string; vendor: string; landed_paise: number | null; qualified: boolean; above_max: boolean; can_cover_alone: boolean };
type Comparison = {
  status: string;
  recommendation: {
    ranked: Ranked[];
    l1_vendor_id: string | null;
    lowest_price_vendor_id: string | null;
    split_proposal: { allocations: { vendor: string; qty_milli: number }[]; shortfall_milli: number; total_paise: number } | null;
  } | null;
};
type Thread = { vendor_id: string; opening_offer_paise: number | null };
type WorkOrder = { id: string; code: string; rfq_id: string; vendor: string; status: string; total_paise: number; qty_display: string };
type Result = { status: string; decision: string | null; message: string | null; work_orders: WorkOrder[] };

export default function Approval({
  rfqId,
  status,
  version,
  qtyMilli,
  unit,
  runnerUp,
  runnerUpReason,
  onChange,
}: {
  rfqId: string;
  status: string;
  version: number;
  qtyMilli: number;
  unit: string;
  runnerUp: string | null;
  runnerUpReason: string | null;
  onChange: () => void;
}) {
  const { me } = useSession();
  const canApprove = me?.kind === "user" && ["owner", "purchase_manager"].includes(me.role);
  const [c, setC] = useState<Comparison | null>(null);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [orders, setOrders] = useState<WorkOrder[]>([]);
  const [override, setOverride] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Comparison>(`/rfqs/${rfqId}/comparison`).then(setC).catch(() => setC(null));
    api<Thread[]>(`/rfqs/${rfqId}/negotiations`).then(setThreads).catch(() => setThreads([]));
    api<WorkOrder[]>("/work-orders")
      .then((all) => setOrders(all.filter((w) => w.rfq_id === rfqId)))
      .catch(() => setOrders([]));
  }, [rfqId]);
  useEffect(() => {
    load();
  }, [load, status]);

  async function act(path: string, json?: unknown) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const r = await api<Result | { ok: boolean }>(path, { method: "POST", json });
      if ("message" in r && r.message) setMessage(r.message);
      if ("ok" in r) setMessage("Sent for review. The owner sees it on their dashboard.");
      onChange();
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed");
      onChange();
    } finally {
      setBusy(false);
    }
  }

  const approve = (choice: string) => act(`/rfqs/${rfqId}/approve`, { idempotency_key: newRef(), choice, version, override_above_max: override });

  const rec = c?.recommendation;
  const byId = new Map((rec?.ranked ?? []).map((r) => [r.vendor_id, r]));
  const l1 = rec?.l1_vendor_id ? byId.get(rec.l1_vendor_id) : undefined;
  const lowest = rec?.lowest_price_vendor_id ? byId.get(rec.lowest_price_vendor_id) : undefined;
  const runner = runnerUp ? byId.get(runnerUp) : undefined;
  const total = (r?: Ranked) => (r?.landed_paise ? Math.round((r.landed_paise * qtyMilli) / 1000) : 0);
  const opening = (r?: Ranked) => threads.find((t) => t.vendor_id === r?.vendor_id)?.opening_offer_paise ?? null;
  const anyAboveMax = (rec?.ranked ?? []).some((r) => r.above_max && r.qualified);

  if (orders.length > 0 && status !== "awaiting_approval") {
    return (
      <div className="box">
        <h2>Work orders</h2>
        <ul>
          {orders.map((w) => (
            <li key={w.id}>
              <Link className="mono" href={`/work-orders/${w.id}`}>
                {w.code}
              </Link>{" "}
              {w.vendor} · {w.qty_display} · {formatINR(w.total_paise)} <span className="status">{statusTag(w.status)}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (status !== "awaiting_approval" || !rec) return null;

  return (
    <div className="box">
      <h2>Recommendation</h2>
      {runner && (
        <p className="box">
          The previous award fell through ({runnerUpReason}). Runner-up: <strong>{runner.vendor}</strong> at {formatINR(runner.landed_paise ?? 0)} per {unit}.{" "}
          {canApprove && (
            <button type="button" disabled={busy} onClick={() => approve(runner.vendor_id)}>
              Approve runner-up
            </button>
          )}
        </p>
      )}
      {runnerUpReason && !runner && (
        <p className="box">
          The previous award fell through ({runnerUpReason}) and no other offer is still valid.{" "}
          {canApprove && (
            <button type="button" className="secondary" disabled={busy} onClick={() => act(`/rfqs/${rfqId}/rebid`)}>
              Re-open bidding
            </button>
          )}
        </p>
      )}
      {l1 ? (
        <table>
          <tbody>
            <tr>
              <th>L1 (best overall score)</th>
              <td>
                <strong>{l1.vendor}</strong>
              </td>
              <td className="num">
                {formatINR(l1.landed_paise ?? 0)} / {unit}
              </td>
              <td className="num">{formatINR(total(l1))} total</td>
            </tr>
            {opening(l1) !== null && (
              <tr>
                <th>Savings vs opening quote</th>
                <td colSpan={3} className="num">
                  {formatINR(Math.round((((opening(l1) ?? 0) - (l1.landed_paise ?? 0)) * qtyMilli) / 1000))} on this order
                </td>
              </tr>
            )}
            {lowest && lowest.vendor_id !== l1.vendor_id && (
              <tr>
                <th>Lowest price (marked separately)</th>
                <td>{lowest.vendor}</td>
                <td className="num">
                  {formatINR(lowest.landed_paise ?? 0)} / {unit}
                </td>
                <td className="num">{formatINR(total(lowest))} total</td>
              </tr>
            )}
          </tbody>
        </table>
      ) : (
        <p>No offer can be recommended as it stands (for example, all are above your maximum price).</p>
      )}
      {rec.split_proposal && rec.split_proposal.shortfall_milli === 0 && (
        <p className="small">
          Split proposal: {rec.split_proposal.allocations.map((a) => `${a.vendor} ${(a.qty_milli / 1000).toLocaleString("en-IN")} ${unit}`).join(", ")}{" "}
          = {formatINR(rec.split_proposal.total_paise)}
        </p>
      )}
      {message && <p className="box">{message}</p>}
      {error && <p className="error">{error}</p>}
      {canApprove ? (
        <p>
          {anyAboveMax && (
            <label className="small">
              <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} /> Allow an offer above my maximum price
            </label>
          )}
          {l1 && l1.can_cover_alone && (
            <button type="button" disabled={busy} onClick={() => approve("l1")}>
              Approve {l1.vendor}
            </button>
          )}{" "}
          {rec.split_proposal && rec.split_proposal.shortfall_milli === 0 && (
            <button type="button" disabled={busy} onClick={() => approve("split")}>
              Approve split
            </button>
          )}{" "}
          <button type="button" className="secondary" disabled={busy} onClick={() => act(`/rfqs/${rfqId}/compare-again`)}>
            Compare again
          </button>{" "}
          <button type="button" className="secondary" disabled={busy} onClick={() => act(`/rfqs/${rfqId}/review`, { note: "" })}>
            Send for review
          </button>
        </p>
      ) : (
        <p>
          <button type="button" className="secondary" disabled={busy} onClick={() => act(`/rfqs/${rfqId}/review`, { note: "" })}>
            Send for review
          </button>{" "}
          <span className="small muted">Only an owner or purchase manager can approve.</span>
        </p>
      )}
    </div>
  );
}
