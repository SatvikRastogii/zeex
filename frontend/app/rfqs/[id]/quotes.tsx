"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatDate, formatINR, formatIST, statusTag } from "@/lib/format";

export type QuoteRow = {
  id: string;
  code: string;
  vendor: string;
  revision: number;
  source: string;
  status: string;
  unit_price_paise: number | null;
  price_unit: string | null;
  price_per_canonical_paise: number | null;
  gst_included: boolean;
  gst_bp: number | null;
  freight_paise: number;
  freight_included: boolean;
  delivery_date: string | null;
  validity_until: string | null;
  payment_terms_days: number | null;
  brand: string | null;
  parse_confidence: number | null;
  flags: Record<string, unknown>;
  has_file: boolean;
  raw_text: string | null;
  received_at: string | null;
};

/** Plain-language flag text for the builder. */
export function flagText(key: string, value: unknown): string {
  const v = value as Record<string, string | number>;
  switch (key) {
    case "arithmetic_mismatch":
      return `Total ${v.stated_total} does not match rate x qty (${v.rate_x_qty} for ${v.qty})`;
    case "possible_typo":
      return `Possible typo: ${v.deviation_pct}% vs reference ${v.reference} (${v.source})`;
    case "suspicious_content":
      return `Suspicious content in document ("${v.excerpt}"). Treated as data; changes nothing.`;
    case "converted":
      return `Converted: ${value}`;
    case "validity_defaulted":
      return `No validity stated; assumed ${formatDate(String(value))}`;
    case "validity_short":
      return `Validity too short (until ${formatDate(String(v.valid_until))}, needed ${formatDate(String(v.needed_until))})`;
    case "rate_list":
      return v.picked ? `Rate list (${v.rows} rows); used "${v.picked}"` : `Rate list (${v.rows} rows); item not found`;
    case "late":
      return String(value);
    case "unit_assumed":
      return `No unit stated; assumed per ${value}`;
    case "gst_assumed_extra":
      return "GST not mentioned; assumed extra";
    case "gst_rate_assumed":
      return `GST rate not stated; assumed ${value}%`;
    case "freight_not_stated":
      return "Freight not mentioned; assumed included";
    case "delivery_date_missing":
      return "No delivery date";
    case "unit_not_convertible":
      return String(value);
    default:
      return key.replace(/_/g, " ");
  }
}

function Original({ q, onClose }: { q: QuoteRow; onClose: () => void }) {
  const url = `/api/quotes/${q.id}/file`;
  const isPdf = q.source === "pdf";
  return (
    <div className="box">
      <p>
        <strong className="mono">{q.code}</strong> from {q.vendor} ·{" "}
        <button type="button" className="secondary" onClick={onClose}>
          Close
        </button>
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)", gap: 12 }}>
        <div>
          <p className="small muted">Original ({q.source})</p>
          {q.has_file && isPdf && <iframe src={url} title={`Original of ${q.code}`} style={{ width: "100%", height: 480, border: "1px solid #000" }} />}
          {q.has_file && !isPdf && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={url} alt={`Original of ${q.code}`} style={{ maxWidth: "100%", border: "1px solid #000" }} />
          )}
          {!q.has_file && <pre className="small" style={{ whiteSpace: "pre-wrap" }}>{q.raw_text ?? "(form submission)"}</pre>}
        </div>
        <div>
          <p className="small muted">Parsed values (confidence {q.parse_confidence ?? "-"}%)</p>
          <table>
            <tbody>
              <tr>
                <th>Rate</th>
                <td className="num">
                  {q.unit_price_paise !== null && formatINR(q.unit_price_paise)} / {q.price_unit}
                </td>
              </tr>
              <tr>
                <th>GST</th>
                <td>
                  {q.gst_included ? "included" : "extra"} @ {(q.gst_bp ?? 0) / 100}%
                </td>
              </tr>
              <tr>
                <th>Freight</th>
                <td>{q.freight_included ? "included" : formatINR(q.freight_paise)}</td>
              </tr>
              <tr>
                <th>Delivery</th>
                <td>{q.delivery_date ? formatDate(q.delivery_date) : "-"}</td>
              </tr>
              <tr>
                <th>Valid until</th>
                <td>{q.validity_until ? formatDate(q.validity_until) : "-"}</td>
              </tr>
              <tr>
                <th>Credit</th>
                <td>{q.payment_terms_days === null ? "-" : `${q.payment_terms_days} days`}</td>
              </tr>
              <tr>
                <th>Brand</th>
                <td>{q.brand ?? "-"}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default function Quotes({ rfqId, canonicalUnit }: { rfqId: string; canonicalUnit: string }) {
  const [rows, setRows] = useState<QuoteRow[]>([]);
  const [open, setOpen] = useState<QuoteRow | null>(null);

  const load = useCallback(() => {
    api<QuoteRow[]>(`/rfqs/${rfqId}/quotes`)
      .then(setRows)
      .catch(() => setRows([]));
  }, [rfqId]);
  useEffect(() => {
    load();
  }, [load]);

  if (rows.length === 0) return null;
  return (
    <>
      <h2>Quotes received</h2>
      <p className="small muted">Only [CONFIRMED] quotes are compared. Parsed documents are confirmed by the vendor first.</p>
      {open && <Original q={open} onClose={() => setOpen(null)} />}
      <table>
        <thead>
          <tr>
            <th>Vendor</th>
            <th>Quote</th>
            <th>Source</th>
            <th className="num">As quoted</th>
            <th className="num">Per {canonicalUnit}</th>
            <th>GST</th>
            <th>Delivery</th>
            <th>Valid until</th>
            <th>Status</th>
            <th>Flags</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((q) => (
            <tr key={q.id}>
              <td>{q.vendor}</td>
              <td className="mono">
                {q.code}
                {q.revision > 1 && <span className="muted"> r{q.revision}</span>}
              </td>
              <td>{q.source}</td>
              <td className="num">
                {q.unit_price_paise !== null ? formatINR(q.unit_price_paise) : "-"}/{q.price_unit}
              </td>
              <td className="num">{q.price_per_canonical_paise !== null ? formatINR(q.price_per_canonical_paise) : "-"}</td>
              <td>{q.gst_included ? "incl" : "extra"}</td>
              <td>{q.delivery_date ? formatDate(q.delivery_date) : "-"}</td>
              <td>{q.validity_until ? formatDate(q.validity_until) : "-"}</td>
              <td className="status">{statusTag(q.status)}</td>
              <td className="small">
                {Object.entries(q.flags).map(([k, v]) => (
                  <div key={k}>{flagText(k, v)}</div>
                ))}
              </td>
              <td>
                <button type="button" className="secondary" onClick={() => setOpen(q)}>
                  {q.has_file ? "Original" : "Details"}
                </button>
                {q.received_at && <div className="small muted">{formatIST(q.received_at)}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
