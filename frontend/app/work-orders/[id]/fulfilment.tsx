"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatINR, formatIST, newRef, statusTag } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { WorkOrderDetail } from "./page";

function qty(milli: number | null, unit: string): string {
  return milli === null ? "-" : `${(milli / 1000).toLocaleString("en-IN")} ${unit}`;
}

export default function Fulfilment({ wo, onChange }: { wo: WorkOrderDetail; onChange: () => void }) {
  const { me } = useSession();
  const role = me?.kind === "user" ? me.role : "";
  const canReceive = ["owner", "purchase_manager", "site_engineer"].includes(role);
  const canApprove = ["owner", "purchase_manager"].includes(role);
  const [error, setError] = useState<string | null>(null);
  const [recvQty, setRecvQty] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [inv, setInv] = useState({ invoice_no: "", po_code: wo.code, amount: "", unit_price: "" });
  const [invFile, setInvFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [acceptNote, setAcceptNote] = useState<Record<string, string>>({});

  const awaiting = wo.deliveries.find((d) => d.status === "dispatched");
  const received = wo.deliveries.reduce((s, d) => s + (d.received_qty_milli ?? 0), 0);
  const short = received < wo.qty_milli;

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed");
    }
  }

  return (
    <>
      {error && <p className="error">{error}</p>}
      <h2>Deliveries</h2>
      {wo.deliveries.length === 0 ? (
        <p className="muted small">Nothing dispatched yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Delivery</th>
              <th>Dispatched</th>
              <th>Vehicle</th>
              <th className="num">Sent</th>
              <th className="num">Received</th>
              <th>Status</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {wo.deliveries.map((d) => (
              <tr key={d.id}>
                <td className="mono">{d.code}</td>
                <td>{d.dispatched_at ? formatIST(d.dispatched_at) : "-"}</td>
                <td className="mono">{d.vehicle_no}</td>
                <td className="num">{qty(d.dispatched_qty_milli, wo.unit)}</td>
                <td className="num">{qty(d.received_qty_milli, wo.unit)}</td>
                <td className="status">{statusTag(d.status)}</td>
                <td className="small">
                  {Object.entries(d.flags).map(([k, v]) => (
                    <div key={k}>{k === "late_days" ? `Arrived ${v} day(s) after the needed-by date` : String(v)}</div>
                  ))}
                  {d.has_photo && (
                    <a href={`/api/deliveries/${d.id}/photo`} target="_blank" rel="noreferrer">
                      Photo
                    </a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="small">
        Received so far: {qty(received, wo.unit)} of {qty(wo.qty_milli, wo.unit)}
      </p>
      {canReceive && awaiting && (
        <form
          className="box"
          onSubmit={(e) => {
            e.preventDefault();
            if (photo) void run(() => api(`/work-orders/${wo.id}/receive?qty=${encodeURIComponent(recvQty)}`, { body: photo }));
          }}
        >
          <strong>Confirm receipt of {awaiting.code}</strong> ({qty(awaiting.dispatched_qty_milli, wo.unit)} dispatched, vehicle {awaiting.vehicle_no})
          <label htmlFor="recv-qty">Quantity received ({wo.unit})</label>
          <input id="recv-qty" inputMode="decimal" required value={recvQty} onChange={(e) => setRecvQty(e.target.value)} size={8} />
          <label htmlFor="recv-photo">Photo of the delivery</label>
          <input id="recv-photo" type="file" accept="image/*" capture="environment" required onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
          <p>
            <button type="submit" disabled={!photo || !recvQty}>
              Confirm receipt
            </button>
          </p>
        </form>
      )}

      <h2>Invoices</h2>
      {wo.invoices.length === 0 && <p className="muted small">No invoice yet.</p>}
      {wo.invoices.map((i) => (
        <div key={i.id} className="box small">
          <strong className="mono">{i.invoice_no}</strong> · {formatINR(i.amount_paise)} · <span className="status">{statusTag(i.status)}</span>
          {Object.values(i.mismatch_flags).map((f) => (
            <div key={f}>{f}</div>
          ))}
          {i.has_file && (
            <div>
              <a href={`/api/invoices/${i.id}/file`}>Invoice file</a>
            </div>
          )}
          {canApprove && i.status === "flagged" && (
            <p>
              <label htmlFor={`acc-${i.id}`}>Accept anyway, with the reason</label>
              <input
                id={`acc-${i.id}`}
                value={acceptNote[i.id] ?? ""}
                onChange={(e) => setAcceptNote((x) => ({ ...x, [i.id]: e.target.value }))}
                size={40}
              />{" "}
              <button
                type="button"
                className="secondary"
                disabled={(acceptNote[i.id] ?? "").trim().length < 3}
                onClick={() => run(() => api(`/invoices/${i.id}/accept`, { json: { note: acceptNote[i.id] } }))}
              >
                Accept invoice
              </button>
            </p>
          )}
        </div>
      ))}
      {canReceive && ["in_delivery", "delivered"].includes(wo.status) && (
        <form
          className="box"
          onSubmit={(e) => {
            e.preventDefault();
            const q = new URLSearchParams({ ...inv, client_ref: newRef(), filename: invFile?.name ?? "invoice.pdf" });
            void run(() => api(`/work-orders/${wo.id}/invoice?${q}`, invFile ? { body: invFile } : { method: "POST" }));
          }}
        >
          <strong>Record the vendor&apos;s invoice</strong> (checked against the work order; mismatches are flagged, never accepted automatically)
          <label htmlFor="inv-no">Invoice number</label>
          <input id="inv-no" required value={inv.invoice_no} onChange={(e) => setInv({ ...inv, invoice_no: e.target.value })} />
          <label htmlFor="inv-po">PO number on the invoice</label>
          <input id="inv-po" required value={inv.po_code} onChange={(e) => setInv({ ...inv, po_code: e.target.value })} />
          <label htmlFor="inv-rate">Unit price on the invoice (₹)</label>
          <input id="inv-rate" inputMode="decimal" required value={inv.unit_price} onChange={(e) => setInv({ ...inv, unit_price: e.target.value })} />
          <label htmlFor="inv-amt">Invoice total (₹)</label>
          <input id="inv-amt" inputMode="decimal" required value={inv.amount} onChange={(e) => setInv({ ...inv, amount: e.target.value })} />
          <label htmlFor="inv-file">Invoice file (optional)</label>
          <input id="inv-file" type="file" accept=".pdf,image/*" onChange={(e) => setInvFile(e.target.files?.[0] ?? null)} />
          <p>
            <button type="submit">Check invoice</button>
          </p>
        </form>
      )}

      {canApprove && ["in_delivery", "delivered"].includes(wo.status) && (
        <div className="box">
          <strong>Close the work order</strong>
          <p className="small">Closing updates the vendor&apos;s ratings and the price history.</p>
          {short && (
            <>
              <label htmlFor="short-note">Shortfall note (required: less than ordered arrived)</label>
              <input id="short-note" value={note} onChange={(e) => setNote(e.target.value)} size={50} />
            </>
          )}
          <p>
            <button
              type="button"
              disabled={short && note.trim().length < 3}
              onClick={() => run(() => api(`/work-orders/${wo.id}/close`, { json: { shortfall_note: short ? note : null } }))}
            >
              Close work order
            </button>
          </p>
        </div>
      )}
    </>
  );
}
