"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { newRef } from "@/lib/format";

const UNITS = ["bag", "tonne", "kg", "cft", "brass", "nos", "box"];

export default function QuoteForm({ rfqId, onDone }: { rfqId: string; onDone: () => void }) {
  const [f, setF] = useState({
    unit_price: "",
    price_unit: "bag",
    gst_included: false,
    gst_percent: "18",
    freight_included: true,
    freight: "",
    delivery_date: "",
    validity_until: "",
    payment_terms_days: "0",
    brand: "",
    qty_offered: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string | boolean) => setF((x) => ({ ...x, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api("/vendor/quotes", {
        json: {
          client_message_id: newRef(),
          rfq_id: rfqId,
          unit_price: f.unit_price,
          price_unit: f.price_unit,
          gst_included: f.gst_included,
          gst_percent: f.gst_percent || null,
          freight_included: f.freight_included,
          freight: f.freight_included ? null : f.freight || null,
          delivery_date: f.delivery_date,
          validity_until: f.validity_until,
          payment_terms_days: Number(f.payment_terms_days),
          brand: f.brand || null,
          qty_offered: f.qty_offered || null,
        },
      });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not submit");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="box small" onSubmit={submit} aria-label="Quote form">
      <strong>Submit quote</strong>
      {error && <p className="error">{error}</p>}
      <label htmlFor="qf-price">Rate (₹)</label>
      <input id="qf-price" inputMode="decimal" required value={f.unit_price} onChange={(e) => set("unit_price", e.target.value)} size={8} />{" "}
      <label htmlFor="qf-unit" style={{ display: "inline" }}>
        per
      </label>{" "}
      <select id="qf-unit" value={f.price_unit} onChange={(e) => set("price_unit", e.target.value)}>
        {UNITS.map((u) => (
          <option key={u}>{u}</option>
        ))}
      </select>
      <label>
        <input type="checkbox" checked={f.gst_included} onChange={(e) => set("gst_included", e.target.checked)} /> Rate includes GST
      </label>
      <label htmlFor="qf-gst">GST %</label>
      <input id="qf-gst" inputMode="decimal" value={f.gst_percent} onChange={(e) => set("gst_percent", e.target.value)} size={4} />
      <label>
        <input type="checkbox" checked={f.freight_included} onChange={(e) => set("freight_included", e.target.checked)} /> Freight included
      </label>
      {!f.freight_included && (
        <>
          <label htmlFor="qf-freight">Freight for the whole order (₹)</label>
          <input id="qf-freight" inputMode="decimal" value={f.freight} onChange={(e) => set("freight", e.target.value)} size={8} />
        </>
      )}
      <label htmlFor="qf-delivery">Delivery date</label>
      <input id="qf-delivery" type="date" required value={f.delivery_date} onChange={(e) => set("delivery_date", e.target.value)} />
      <label htmlFor="qf-validity">Quote valid until</label>
      <input id="qf-validity" type="date" required value={f.validity_until} onChange={(e) => set("validity_until", e.target.value)} />
      <label htmlFor="qf-credit">Credit days (0 = advance/cash)</label>
      <input id="qf-credit" inputMode="numeric" value={f.payment_terms_days} onChange={(e) => set("payment_terms_days", e.target.value)} size={4} />
      <label htmlFor="qf-brand">Brand (optional)</label>
      <input id="qf-brand" value={f.brand} onChange={(e) => set("brand", e.target.value)} maxLength={60} />
      <label htmlFor="qf-qty">Quantity you can supply (optional, blank = full)</label>
      <input id="qf-qty" inputMode="decimal" value={f.qty_offered} onChange={(e) => set("qty_offered", e.target.value)} size={8} />
      <p>
        <button type="submit" disabled={busy}>
          Submit quote
        </button>{" "}
        <button type="button" className="secondary" onClick={onDone}>
          Close
        </button>
      </p>
    </form>
  );
}
