"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatDate, statusTag } from "@/lib/format";
import Guard from "../../guard";

type Line = {
  id: string;
  line_no: number;
  item: { code: string; name: string } | null;
  raw_text: string;
  spec: string | null;
  qty_display: string;
  entered: string;
  needed_by: string;
  partial_allowed: boolean;
  rfq: { id: string; code: string; status: string; stale: boolean; revision: number } | null;
};
type Bom = {
  id: string;
  code: string;
  title: string | null;
  status: string;
  revision: number;
  has_file: boolean;
  site: { name: string; area: string };
  lines: Line[];
};

const EDITABLE = new Set(["validated", "published", "in_progress"]);
const LOCKED_RFQ = new Set(["awaiting_approval", "awarded", "closed", "cancelled"]);

function LineEditor({ bom, line, onSaved }: { bom: Bom; line: Line; onSaved: (b: Bom) => void }) {
  const [open, setOpen] = useState(false);
  const [qty, setQty] = useState(line.entered.split(" ")[0]);
  const [unit, setUnit] = useState(line.entered.split(" ").slice(1).join(" "));
  const [date, setDate] = useState(line.needed_by);
  const [error, setError] = useState<string | null>(null);

  if (!EDITABLE.has(bom.status) || (line.rfq && LOCKED_RFQ.has(line.rfq.status))) return null;
  if (!open)
    return (
      <button type="button" className="secondary" onClick={() => setOpen(true)}>
        Edit
      </button>
    );

  async function save() {
    setError(null);
    try {
      onSaved(await api<Bom>(`/boms/${bom.id}/lines/${line.id}`, { method: "PATCH", json: { quantity: qty, unit, needed_by: date } }));
      setOpen(false);
    } catch (e) {
      const d = e instanceof ApiError ? (e.detail as { rows?: { errors: { message: string }[] }[] }) : null;
      setError(d?.rows?.[0]?.errors.map((x) => x.message).join(" ") ?? (e instanceof Error ? e.message : "Save failed"));
    }
  }

  return (
    <div>
      <input aria-label="Quantity" size={6} value={qty} onChange={(e) => setQty(e.target.value)} />{" "}
      <input aria-label="Unit" size={6} value={unit} onChange={(e) => setUnit(e.target.value)} />{" "}
      <input aria-label="Needed by" type="date" value={date} onChange={(e) => setDate(e.target.value)} />{" "}
      <button type="button" onClick={save}>
        Save
      </button>{" "}
      <button type="button" className="secondary" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {bom.status !== "validated" && <div className="small">Saving creates a new revision; invited vendors are re-sent the change.</div>}
      {error && <div className="small">{error}</div>}
    </div>
  );
}

function BomDetail() {
  const { id } = useParams<{ id: string }>();
  const [bom, setBom] = useState<Bom | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Bom>(`/boms/${id}`)
      .then(setBom)
      .catch((e) => setError(e.message));
  }, [id]);

  useEffect(load, [load]);

  async function act(path: string, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setError(null);
    try {
      setBom(await api<Bom>(`/boms/${id}/${path}`, { method: "POST" }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    }
  }

  if (error && !bom) return <p className="error">{error}</p>;
  if (!bom) return <p className="muted">Loading...</p>;

  return (
    <>
      <p className="small">
        <Link href="/">Dashboard</Link>
      </p>
      <h1>
        <span className="mono">{bom.code}</span> {bom.title && `· ${bom.title}`} <span className="status">{statusTag(bom.status)}</span>
      </h1>
      <p className="small">
        Site: {bom.site.name} · Revision {bom.revision}
        {bom.has_file && (
          <>
            {" "}
            · <a href={`/api/boms/${bom.id}/file`}>Original file</a>
          </>
        )}
      </p>
      {error && <p className="error">{error}</p>}
      <p>
        {bom.status === "validated" && (
          <button type="button" onClick={() => act("publish")}>
            Publish
          </button>
        )}{" "}
        {!["awarded", "closed", "cancelled"].includes(bom.status) && (
          <button type="button" className="secondary" onClick={() => act("cancel", `Cancel ${bom.code}? Vendors in open RFQs will be told.`)}>
            Cancel BOM
          </button>
        )}
      </p>
      <table>
        <thead>
          <tr>
            <th className="num">#</th>
            <th>Item</th>
            <th className="num">Quantity</th>
            <th>Entered as</th>
            <th>Needed by</th>
            <th>Partial</th>
            <th>RFQ</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {bom.lines.map((ln) => (
            <tr key={ln.id}>
              <td className="num">{ln.line_no}</td>
              <td>
                {ln.item?.name ?? ln.raw_text}
                {ln.spec && <span className="muted"> ({ln.spec})</span>}
              </td>
              <td className="num">{ln.qty_display}</td>
              <td className="small">{ln.entered}</td>
              <td>{formatDate(ln.needed_by)}</td>
              <td>{ln.partial_allowed ? "yes" : "no"}</td>
              <td className="small">
                {ln.rfq ? (
                  <>
                    <Link className="mono" href={`/rfqs/${ln.rfq.id}`}>{ln.rfq.code}</Link> <span className="status">{statusTag(ln.rfq.status)}</span>
                    {ln.rfq.stale && <span className="status"> [CHANGED rev {ln.rfq.revision}]</span>}
                  </>
                ) : (
                  <span className="muted">after publish</span>
                )}
              </td>
              <td>
                <LineEditor bom={bom} line={ln} onSaved={setBom} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export default function BomPage() {
  return (
    <Guard need="builder">
      <BomDetail />
    </Guard>
  );
}
