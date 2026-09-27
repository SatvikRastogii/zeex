"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatDate, statusTag } from "@/lib/format";
import { useSession } from "@/lib/session";
import Guard from "../../guard";

type ShortlistRow = { vendor_id: string; vendor: string; score: number; reason: string; added_by_builder: boolean; status: string };
type Rfq = {
  id: string;
  code: string;
  status: string;
  revision: number;
  stale: boolean;
  bom: { id: string; code: string };
  site: { name: string; area: string };
  line: { line_no: number; item: { name: string }; qty_display: string; needed_by: string; partial_allowed: boolean };
  match_report: {
    excluded?: { vendor: string; reason: string }[];
    warning?: string | null;
    suggestions?: string[];
    extra_radius_km?: number;
    eligible?: number;
  };
  shortlist: ShortlistRow[];
};
type Candidate = { vendor_id: string; vendor: string; eligible: boolean; reason: string };

const EDITABLE = new Set(["draft", "matching", "no_vendors_matched"]);

function Matching({ rfq, onChange }: { rfq: Rfq; onChange: (r: Rfq) => void }) {
  const { me } = useSession();
  const canEdit = me?.kind === "user" && ["owner", "purchase_manager"].includes(me.role) && EDITABLE.has(rfq.status);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [pick, setPick] = useState("");
  const [error, setError] = useState<string | null>(null);
  const report = rfq.match_report;
  const proposed = rfq.shortlist.filter((s) => s.status === "proposed");
  const removed = rfq.shortlist.filter((s) => s.status === "removed");

  useEffect(() => {
    if (canEdit)
      api<Candidate[]>(`/rfqs/${rfq.id}/candidates`)
        .then(setCandidates)
        .catch(() => setCandidates([]));
  }, [canEdit, rfq]);

  async function run(path: string, json: unknown) {
    setError(null);
    try {
      onChange(await api<Rfq>(`/rfqs/${rfq.id}/${path}`, { json }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    }
  }

  return (
    <>
      <h2>Matching review</h2>
      {error && <p className="error">{error}</p>}
      {report.warning && (
        <div className="box">
          <strong>{report.warning}</strong>
          <ul className="small">
            {report.suggestions?.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          {canEdit && (
            <p>
              <button type="button" className="secondary" onClick={() => run("match", { extra_radius_km: (report.extra_radius_km ?? 0) + 10 })}>
                Widen radius by 10 km
              </button>{" "}
              {!rfq.line.partial_allowed && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => run("match", { extra_radius_km: report.extra_radius_km ?? 0, allow_partial: true })}
                >
                  Allow partial supply
                </button>
              )}
            </p>
          )}
        </div>
      )}
      <p className="small muted">
        {proposed.length} vendor(s) on the shortlist · {report.eligible ?? 0} eligible
        {report.extra_radius_km ? ` · radius widened by ${report.extra_radius_km} km` : ""}
      </p>
      <table>
        <thead>
          <tr>
            <th>Vendor</th>
            <th className="num">Score</th>
            <th>Why</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {[...proposed, ...removed].map((s) => (
            <tr key={s.vendor_id}>
              <td>{s.vendor}</td>
              <td className="num">{s.score}</td>
              <td className="small">
                {s.reason}
                {s.added_by_builder && " · added by you"}
              </td>
              <td className="status">{statusTag(s.status)}</td>
              <td>
                {canEdit && s.status === "proposed" && (
                  <button type="button" className="secondary" onClick={() => run("shortlist", { vendor_id: s.vendor_id, action: "remove" })}>
                    Remove
                  </button>
                )}
                {canEdit && s.status === "removed" && (
                  <button type="button" className="secondary" onClick={() => run("shortlist", { vendor_id: s.vendor_id, action: "add" })}>
                    Restore
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {canEdit && candidates.length > 0 && (
        <p>
          <label htmlFor="add-vendor">Add a vendor</label>
          <select id="add-vendor" value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">Choose...</option>
            {candidates.map((c) => (
              <option key={c.vendor_id} value={c.vendor_id} disabled={!c.eligible}>
                {c.vendor} {c.eligible ? "" : `(not eligible: ${c.reason})`}
              </option>
            ))}
          </select>{" "}
          <button type="button" className="secondary" disabled={!pick} onClick={() => run("shortlist", { vendor_id: pick, action: "add" }).then(() => setPick(""))}>
            Add
          </button>
        </p>
      )}
      {report.excluded && report.excluded.length > 0 && (
        <>
          <h3>Not matched</h3>
          <table>
            <tbody>
              {report.excluded.map((e) => (
                <tr key={e.vendor}>
                  <td>{e.vendor}</td>
                  <td className="small">{e.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}

function RfqPage() {
  const { id } = useParams<{ id: string }>();
  const [rfq, setRfq] = useState<Rfq | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Rfq>(`/rfqs/${id}`)
      .then(setRfq)
      .catch((e) => setError(e.message));
  }, [id]);
  useEffect(load, [load]);

  if (error && !rfq) return <p className="error">{error}</p>;
  if (!rfq) return <p className="muted">Loading...</p>;

  return (
    <>
      <p className="small">
        <Link href="/">Dashboard</Link> / <Link href={`/boms/${rfq.bom.id}`}>{rfq.bom.code}</Link>
      </p>
      <h1>
        <span className="mono">{rfq.code}</span> <span className="status">{statusTag(rfq.status)}</span>
      </h1>
      <p>
        Line {rfq.line.line_no}: <strong>{rfq.line.item.name}</strong> · <span className="mono">{rfq.line.qty_display}</span> · needed by{" "}
        {formatDate(rfq.line.needed_by)} · {rfq.site.name} · partial {rfq.line.partial_allowed ? "allowed" : "not allowed"}
      </p>
      <Matching rfq={rfq} onChange={setRfq} />
    </>
  );
}

export default function Page() {
  return (
    <Guard need="builder">
      <RfqPage />
    </Guard>
  );
}
