"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatDate, formatINR, statusTag } from "@/lib/format";
import Guard from "../guard";

type WorkOrder = {
  id: string;
  code: string;
  rfq_code: string;
  vendor: string;
  item: string;
  qty_display: string;
  total_paise: number;
  delivery_date: string | null;
  status: string;
};

function List() {
  const [rows, setRows] = useState<WorkOrder[] | null>(null);
  useEffect(() => {
    api<WorkOrder[]>("/work-orders")
      .then(setRows)
      .catch(() => setRows([]));
  }, []);
  if (rows === null) return <p className="muted">Loading...</p>;
  return (
    <>
      <h1>Work orders</h1>
      {rows.length === 0 ? (
        <p className="muted">No work orders yet. They are created when you approve a recommendation.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>PO</th>
              <th>RFQ</th>
              <th>Vendor</th>
              <th>Item</th>
              <th className="num">Quantity</th>
              <th className="num">Total</th>
              <th>Delivery</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((w) => (
              <tr key={w.id}>
                <td className="mono">
                  <Link href={`/work-orders/${w.id}`}>{w.code}</Link>
                </td>
                <td className="mono">{w.rfq_code}</td>
                <td>{w.vendor}</td>
                <td>{w.item}</td>
                <td className="num">{w.qty_display}</td>
                <td className="num">{formatINR(w.total_paise)}</td>
                <td>{w.delivery_date ? formatDate(w.delivery_date) : "-"}</td>
                <td className="status">{statusTag(w.status)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

export default function WorkOrdersPage() {
  return (
    <Guard need="builder">
      <List />
    </Guard>
  );
}
