"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatDate, formatINR, formatIST, rupeesToPaise, statusTag } from "@/lib/format";
import { useSession } from "@/lib/session";
import { flagText } from "./quotes";

type Ranked = {
  quote_id: string;
  code: string;
  vendor_id: string;
  vendor: string;
  landed_paise: number | null;
  qualified: boolean;
  reasons: string[];
  above_max: boolean;
  can_cover_alone: boolean;
  supply_milli: number;
  parts: Record<string, number>;
  score: number;
  delivery_date: string | null;
  payment_terms_days: number;
  shortlisted: boolean;
  flags: Record<string, unknown>;
};
type Split = {
  allocations: { vendor: string; qty_milli: number; landed_paise: number; total_paise: number }[];
  skipped: { vendor: string; reason: string }[];
  covered_milli: number;
  shortfall_milli: number;
  total_paise: number;
  note?: string;
  options?: string[];
};
type Comparison = {
  status: string;
  weights: Record<string, number>;
  gst_mode: string;
  window_extended: boolean;
  single_quote: boolean;
  target_price_paise: number | null;
  max_price_paise: number | null;
  recommendation: {
    generated_at: string;
    ranked: Ranked[];
    l1_vendor_id: string | null;
    lowest_price_vendor_id: string | null;
    split_proposal: Split | null;
  } | null;
};

const PARTS = ["price", "delivery", "payment", "reliability", "quality"];

function qty(milli: number, unit: string): string {
  return `${(milli / 1000).toLocaleString("en-IN")} ${unit}`;
}

function Limits({ rfqId, c, onChange }: { rfqId: string; c: Comparison; onChange: (c: Comparison) => void }) {
  const [target, setTarget] = useState(c.target_price_paise ? String(c.target_price_paise / 100) : "");
  const [max, setMax] = useState(c.max_price_paise ? String(c.max_price_paise / 100) : "");
  const [error, setError] = useState<string | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      onChange(
        await api<Comparison>(`/rfqs/${rfqId}/limits`, {
          method: "PUT",
          json: { target_price_paise: rupeesToPaise(target), max_price_paise: rupeesToPaise(max) },
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  return (
    <form className="box small" onSubmit={save}>
      <strong>Private price limits</strong> (landed, per unit). Never shown to vendors and never sent to the AI.
      <br />
      <label htmlFor="target" style={{ display: "inline" }}>
        Target ₹
      </label>{" "}
      <input id="target" inputMode="decimal" size={8} value={target} onChange={(e) => setTarget(e.target.value)} />{" "}
      <label htmlFor="max" style={{ display: "inline" }}>
        Maximum ₹
      </label>{" "}
      <input id="max" inputMode="decimal" size={8} value={max} onChange={(e) => setMax(e.target.value)} />{" "}
      <button type="submit" className="secondary">
        Save
      </button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default function ComparisonView({ rfqId, unit, qtyMilli }: { rfqId: string; unit: string; qtyMilli: number }) {
  const { me } = useSession();
  const canEdit = me?.kind === "user" && ["owner", "purchase_manager"].includes(me.role);
  const [c, setC] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Comparison>(`/rfqs/${rfqId}/comparison`)
      .then(setC)
      .catch(() => setC(null));
  }, [rfqId]);
  useEffect(() => {
    load();
  }, [load]);

  async function rescore() {
    setError(null);
    try {
      setC(await api<Comparison>(`/rfqs/${rfqId}/evaluate`, { method: "POST" }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    }
  }

  if (!c) return null;
  const rec = c.recommendation;
  return (
    <>
      <h2>Quotes comparison</h2>
      {canEdit && <Limits rfqId={rfqId} c={c} onChange={setC} />}
      {c.window_extended && c.status === "bidding" && (
        <p className="box">Fewer than 2 confirmed quotes at the close, so the bid window was extended once and vendors were reminded.</p>
      )}
      {c.single_quote && (
        <p className="box">Only one confirmed quote. There is no competition, so the agent will not negotiate as if there were. It goes straight to you.</p>
      )}
      {!rec ? (
        <p className="muted small">The comparison appears when the bid window closes.</p>
      ) : (
        <>
          <p className="small">
            <strong>L1 here = best overall score. The lowest price is marked separately.</strong> Landed cost per {unit}{" "}
            {c.gst_mode === "incl" ? "including" : "excluding"} GST, plus freight and unloading. Weights:{" "}
            {PARTS.map((p) => `${p} ${c.weights[p]}%`).join(", ")}. Scored {formatIST(rec.generated_at)}.{" "}
            {canEdit && ["evaluating", "negotiating", "awaiting_approval"].includes(c.status) && (
              <button type="button" className="secondary" onClick={rescore}>
                Score again
              </button>
            )}
          </p>
          {error && <p className="error">{error}</p>}
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th className="num">#</th>
                  <th>Vendor</th>
                  <th className="num">Landed / {unit}</th>
                  <th className="num">Total</th>
                  <th>Delivery</th>
                  <th className="num">Credit</th>
                  {PARTS.map((p) => (
                    <th key={p} className="num">
                      {p}
                    </th>
                  ))}
                  <th className="num">Score</th>
                  <th>Marks</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {rec.ranked.map((r, i) => (
                  <tr key={r.quote_id}>
                    <td className="num">{r.qualified ? i + 1 : "-"}</td>
                    <td>
                      {r.vendor}
                      <div className="mono small muted">{r.code}</div>
                    </td>
                    <td className="num">{r.landed_paise !== null ? formatINR(r.landed_paise) : "-"}</td>
                    <td className="num">
                      {r.landed_paise === null ? "-" : r.can_cover_alone ? formatINR(Math.round((r.landed_paise * qtyMilli) / 1000)) : "part qty"}
                    </td>
                    <td>{r.delivery_date ? formatDate(r.delivery_date) : "-"}</td>
                    <td className="num">{r.payment_terms_days}d</td>
                    {PARTS.map((p) => (
                      <td key={p} className="num">
                        {r.qualified ? ((r.parts[p] ?? 0) / 100).toFixed(1) : "-"}
                      </td>
                    ))}
                    <td className="num">{r.qualified ? r.score.toFixed(2) : "-"}</td>
                    <td className="status">
                      {r.vendor_id === rec.l1_vendor_id && <div>[L1]</div>}
                      {r.vendor_id === rec.lowest_price_vendor_id && <div>[LOWEST PRICE]</div>}
                      {r.shortlisted && <div>[SHORTLISTED]</div>}
                      {r.above_max && <div>[ABOVE MAX]</div>}
                      {!r.qualified && <div>{statusTag("disqualified")}</div>}
                      {r.qualified && !r.can_cover_alone && <div>[PART QTY]</div>}
                    </td>
                    <td className="small">
                      {r.reasons.map((x) => (
                        <div key={x}>{x}</div>
                      ))}
                      {!r.can_cover_alone && r.qualified && <div>Can supply {qty(r.supply_milli, unit)}</div>}
                      {Object.entries(r.flags)
                        .filter(([k]) => ["arithmetic_mismatch", "possible_typo", "suspicious_content"].includes(k))
                        .map(([k, v]) => (
                          <div key={k}>{flagText(k, v)}</div>
                        ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rec.split_proposal && (
            <div className="box">
              <h3>Split-award proposal</h3>
              <p className="small">No single vendor can supply the full {qty(qtyMilli, unit)}. Allocated by score within each vendor&apos;s capacity:</p>
              <table>
                <thead>
                  <tr>
                    <th>Vendor</th>
                    <th className="num">Quantity</th>
                    <th className="num">Landed / {unit}</th>
                    <th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {rec.split_proposal.allocations.map((a) => (
                    <tr key={a.vendor}>
                      <td>{a.vendor}</td>
                      <td className="num">{qty(a.qty_milli, unit)}</td>
                      <td className="num">{formatINR(a.landed_paise)}</td>
                      <td className="num">{formatINR(a.total_paise)}</td>
                    </tr>
                  ))}
                  <tr>
                    <th>Covered</th>
                    <td className="num">{qty(rec.split_proposal.covered_milli, unit)}</td>
                    <td></td>
                    <td className="num">{formatINR(rec.split_proposal.total_paise)}</td>
                  </tr>
                </tbody>
              </table>
              {rec.split_proposal.skipped.map((s) => (
                <p key={s.vendor} className="small">
                  Skipped {s.vendor}: {s.reason}
                </p>
              ))}
              {rec.split_proposal.shortfall_milli > 0 && (
                <p className="box">
                  <strong>Shortfall: {qty(rec.split_proposal.shortfall_milli, unit)}</strong> cannot be covered by the quotes received. Options:{" "}
                  {rec.split_proposal.options?.join("; ")}.
                </p>
              )}
              {rec.split_proposal.note && <p className="small">{rec.split_proposal.note}</p>}
            </div>
          )}
        </>
      )}
    </>
  );
}
