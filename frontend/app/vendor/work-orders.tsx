"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatINR, newRef, statusTag } from "@/lib/format";

type Po = {
  id: string;
  code: string;
  rfq_id: string;
  item: string;
  unit: string;
  qty_milli: number;
  qty_display: string;
  total_paise: number;
  status: string;
  received_milli: number;
};

/** The vendor's work orders: confirm, then dispatch (Simulated WhatsApp actions). */
export default function VendorWorkOrders() {
  const [rows, setRows] = useState<Po[]>([]);
  const [vehicle, setVehicle] = useState<Record<string, string>>({});
  const [qty, setQty] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Po[]>("/vendor/work-orders")
      .then(setRows)
      .catch(() => setRows([]));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed");
    }
  }

  if (rows.length === 0) return null;
  return (
    <div className="box">
      <h2>My work orders</h2>
      {error && <p className="error">{error}</p>}
      <table>
        <thead>
          <tr>
            <th>PO</th>
            <th>Item</th>
            <th className="num">Quantity</th>
            <th className="num">Total</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((w) => (
            <tr key={w.id}>
              <td className="mono">{w.code}</td>
              <td>{w.item}</td>
              <td className="num">{w.qty_display}</td>
              <td className="num">{formatINR(w.total_paise)}</td>
              <td className="status">{statusTag(w.status)}</td>
              <td>
                {w.status === "issued" &&
                  ["Confirm", "Decline"].map((b) => (
                    <button
                      key={b}
                      type="button"
                      className={b === "Confirm" ? "" : "secondary"}
                      onClick={() =>
                        run(() => api("/vendor/messages", { json: { client_message_id: newRef(), rfq_id: w.rfq_id, button: b, text: b } }))
                      }
                    >
                      {b}
                    </button>
                  ))}
                {["vendor_confirmed", "in_delivery"].includes(w.status) && (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      void run(() =>
                        api(`/vendor/work-orders/${w.id}/dispatch`, {
                          json: { client_ref: newRef(), vehicle_no: vehicle[w.id] ?? "", qty: qty[w.id] || null },
                        }),
                      );
                    }}
                  >
                    <label htmlFor={`veh-${w.id}`} className="small">
                      Vehicle no.
                    </label>
                    <input id={`veh-${w.id}`} size={12} required value={vehicle[w.id] ?? ""} onChange={(e) => setVehicle({ ...vehicle, [w.id]: e.target.value })} />{" "}
                    <label htmlFor={`qty-${w.id}`} className="small" style={{ display: "inline" }}>
                      Qty ({w.unit}, blank = all)
                    </label>{" "}
                    <input id={`qty-${w.id}`} size={6} inputMode="decimal" value={qty[w.id] ?? ""} onChange={(e) => setQty({ ...qty, [w.id]: e.target.value })} />{" "}
                    <button type="submit">Mark dispatched</button>
                  </form>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
