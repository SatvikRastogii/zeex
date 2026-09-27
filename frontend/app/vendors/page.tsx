"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { statusTag } from "@/lib/format";
import { useSession } from "@/lib/session";
import Guard from "../guard";

type Vendor = {
  id: string;
  name: string;
  phone: string;
  gstin: string | null;
  categories: string[];
  brands: string[];
  credit_days: number;
  on_time_bp: number;
  qty_accuracy_bp: number;
  invoice_match_bp: number;
  response_bp: number;
  orders_completed: number;
  opted_out: boolean;
  link_status: string;
  notes: string | null;
};

const pct = (bp: number) => `${Math.round(bp / 100)}%`;

function Directory() {
  const { me } = useSession();
  const canEdit = me?.kind === "user" && ["owner", "purchase_manager"].includes(me.role);
  const [rows, setRows] = useState<Vendor[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Vendor[]>("/vendors")
      .then(setRows)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  async function toggle(v: Vendor) {
    const status = v.link_status === "active" ? "blocked" : "active";
    if (status === "blocked" && !window.confirm(`Block ${v.name}? They will not be matched to your RFQs.`)) return;
    try {
      await api(`/vendors/${v.id}`, { method: "PATCH", json: { status, notes: v.notes } });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    }
  }

  return (
    <>
      <h1>Vendors</h1>
      <p className="small muted">Vendors linked to your organisation. Ratings update when work orders close.</p>
      {error && <p className="error">{error}</p>}
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Vendor</th>
              <th>Categories</th>
              <th>GSTIN</th>
              <th className="num">Orders</th>
              <th className="num">On time</th>
              <th className="num">Qty accuracy</th>
              <th className="num">Invoice match</th>
              <th className="num">Responds</th>
              <th className="num">Credit</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <tr key={v.id}>
                <td>
                  {v.name}
                  <div className="mono small muted">{v.phone}</div>
                </td>
                <td>{v.categories.join(", ")}</td>
                <td className="mono small">{v.gstin ?? "missing"}</td>
                <td className="num">{v.orders_completed}</td>
                <td className="num">{pct(v.on_time_bp)}</td>
                <td className="num">{pct(v.qty_accuracy_bp)}</td>
                <td className="num">{pct(v.invoice_match_bp)}</td>
                <td className="num">{pct(v.response_bp)}</td>
                <td className="num">{v.credit_days}d</td>
                <td className="status">
                  {statusTag(v.link_status)}
                  {v.opted_out && <div>[OPTED OUT]</div>}
                </td>
                <td>
                  {canEdit && (
                    <button type="button" className="secondary" onClick={() => toggle(v)}>
                      {v.link_status === "active" ? "Block" : "Unblock"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function VendorsPage() {
  return (
    <Guard need="builder">
      <Directory />
    </Guard>
  );
}
