"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatDate, formatINR, formatIST, statusTag } from "@/lib/format";
import { useSession } from "@/lib/session";
import Guard from "../../guard";
import Fulfilment from "./fulfilment";

export type WorkOrderDetail = {
  id: string;
  code: string;
  rfq_id: string;
  rfq_code: string;
  vendor: string;
  item: string;
  unit: string;
  qty_milli: number;
  qty_display: string;
  unit_price_paise: number;
  subtotal_paise: number;
  gst_paise: number;
  freight_paise: number;
  total_paise: number;
  delivery_date: string | null;
  status: string;
  confirm_by_display: string | null;
  confirmed_at: string | null;
  shortfall_note: string | null;
  deliveries: {
    id: string;
    code: string;
    status: string;
    dispatched_at: string | null;
    vehicle_no: string | null;
    dispatched_qty_milli: number | null;
    received_qty_milli: number | null;
    received_at: string | null;
    flags: Record<string, unknown>;
    has_photo: boolean;
  }[];
  invoices: {
    id: string;
    invoice_no: string | null;
    amount_paise: number;
    unit_price_paise: number | null;
    status: string;
    mismatch_flags: Record<string, string>;
    has_file: boolean;
  }[];
};

const CANCELLABLE = new Set(["issued", "vendor_confirmed", "in_delivery"]);

function Detail() {
  const { id } = useParams<{ id: string }>();
  const { me } = useSession();
  const canApprove = me?.kind === "user" && ["owner", "purchase_manager"].includes(me.role);
  const [wo, setWo] = useState<WorkOrderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<WorkOrderDetail>(`/work-orders/${id}`)
      .then(setWo)
      .catch((e) => setError(e.message));
  }, [id]);
  useEffect(() => {
    load();
  }, [load]);

  async function cancel() {
    if (!wo || !window.confirm(`Cancel ${wo.code}? The vendor is told and their capacity is released.`)) return;
    try {
      await api(`/work-orders/${id}/cancel`, { method: "POST" });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    }
  }

  if (error && !wo) return <p className="error">{error}</p>;
  if (!wo) return <p className="muted">Loading...</p>;
  return (
    <>
      <p className="small">
        <Link href="/work-orders">Work orders</Link> / <Link href={`/rfqs/${wo.rfq_id}`}>{wo.rfq_code}</Link>
      </p>
      <h1>
        <span className="mono">{wo.code}</span> <span className="status">{statusTag(wo.status)}</span>
      </h1>
      {error && <p className="error">{error}</p>}
      <table>
        <tbody>
          <tr>
            <th>Vendor</th>
            <td>{wo.vendor}</td>
          </tr>
          <tr>
            <th>Item</th>
            <td>
              {wo.item}, {wo.qty_display} at {formatINR(wo.unit_price_paise)} per {wo.unit}
            </td>
          </tr>
          <tr>
            <th>Amounts</th>
            <td className="num">
              Subtotal {formatINR(wo.subtotal_paise)} · GST {formatINR(wo.gst_paise)} · Freight {formatINR(wo.freight_paise)} ·{" "}
              <strong>Total {formatINR(wo.total_paise)}</strong>
            </td>
          </tr>
          <tr>
            <th>Promised delivery</th>
            <td>{wo.delivery_date ? formatDate(wo.delivery_date) : "-"}</td>
          </tr>
          <tr>
            <th>Vendor confirmation</th>
            <td>
              {wo.confirmed_at
                ? `Confirmed ${formatIST(wo.confirmed_at)}`
                : wo.status === "issued"
                  ? `Waiting; must confirm by ${wo.confirm_by_display}`
                  : statusTag(wo.status)}
            </td>
          </tr>
          {wo.shortfall_note && (
            <tr>
              <th>Shortfall</th>
              <td>{wo.shortfall_note}</td>
            </tr>
          )}
        </tbody>
      </table>
      <p>
        <a className="btn secondary" href={`/api/work-orders/${wo.id}/pdf`} target="_blank" rel="noreferrer">
          Work order PDF
        </a>{" "}
        {canApprove && CANCELLABLE.has(wo.status) && (
          <button type="button" className="secondary" onClick={cancel}>
            Cancel work order
          </button>
        )}
      </p>
      <Fulfilment wo={wo} onChange={load} />
    </>
  );
}

export default function WorkOrderPage() {
  return (
    <Guard need="builder">
      <Detail />
    </Guard>
  );
}
