"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import Guard from "../guard";

type Entry = { at: string; at_display: string; actor: string; action: string; entity: string; entity_id: string | null; after: unknown };

const ENTITIES = ["", "bom", "rfq", "quote", "negotiation", "work_order", "delivery", "invoice", "vendor", "user", "builder_org"];

function Log() {
  const [rows, setRows] = useState<Entry[]>([]);
  const [entity, setEntity] = useState("");
  const [action, setAction] = useState("");

  const load = useCallback((before?: string) => {
    const q = new URLSearchParams({ limit: "100" });
    if (entity) q.set("entity", entity);
    if (action) q.set("action", action);
    if (before) q.set("before", before);
    api<Entry[]>(`/audit?${q}`)
      .then((r) => setRows((old) => (before ? [...old, ...r] : r)))
      .catch(() => setRows([]));
  }, [entity, action]);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <h1>Audit log</h1>
      <p className="small muted">Read-only. Every state change and decision is recorded; entries can never be edited or deleted.</p>
      <p>
        <label htmlFor="entity" style={{ display: "inline" }}>
          Entity
        </label>{" "}
        <select id="entity" value={entity} onChange={(e) => setEntity(e.target.value)}>
          {ENTITIES.map((e) => (
            <option key={e} value={e}>
              {e || "all"}
            </option>
          ))}
        </select>{" "}
        <label htmlFor="action" style={{ display: "inline" }}>
          Action contains
        </label>{" "}
        <input id="action" value={action} onChange={(e) => setAction(e.target.value)} size={16} />
      </p>
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Who</th>
            <th>Action</th>
            <th>Entity</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.at}-${i}`}>
              <td className="mono small">{r.at_display}</td>
              <td className="small">{r.actor}</td>
              <td className="mono small">{r.action}</td>
              <td className="small">{r.entity}</td>
              <td className="mono small" style={{ maxWidth: 420, overflowWrap: "anywhere" }}>
                {r.after ? JSON.stringify(r.after) : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length >= 100 && (
        <p>
          <button type="button" className="secondary" onClick={() => load(rows[rows.length - 1].at)}>
            Older entries
          </button>
        </p>
      )}
    </>
  );
}

export default function AuditPage() {
  return (
    <Guard need="builder">
      <Log />
    </Guard>
  );
}
