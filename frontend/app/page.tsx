"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatIST, statusTag } from "@/lib/format";
import Guard from "./guard";

type BomRow = { id: string; code: string; title: string | null; status: string; revision: number; site: string; lines: number; created_at: string };

const CLOSED = new Set(["closed", "cancelled"]);

function Dashboard() {
  const [boms, setBoms] = useState<BomRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<BomRow[]>("/boms")
      .then(setBoms)
      .catch((e) => setError(e.message));
  }, []);

  const open = (boms ?? []).filter((b) => !CLOSED.has(b.status));
  const counts = open.reduce<Record<string, number>>((acc, b) => ({ ...acc, [b.status]: (acc[b.status] ?? 0) + 1 }), {});

  return (
    <>
      <h1>Dashboard</h1>
      <p>
        <Link className="btn" href="/boms/new">
          New BOM
        </Link>
      </p>
      {error && <p className="error">{error}</p>}
      <h2>Open BOMs</h2>
      {Object.keys(counts).length > 0 && (
        <p className="small">
          {Object.entries(counts)
            .map(([s, n]) => `${statusTag(s)} ${n}`)
            .join("   ")}
        </p>
      )}
      {boms === null ? (
        <p className="muted">Loading...</p>
      ) : boms.length === 0 ? (
        <p className="muted">No BOMs yet. Create one to start.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>BOM</th>
              <th>Title</th>
              <th>Site</th>
              <th className="num">Lines</th>
              <th>Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {boms.map((b) => (
              <tr key={b.id}>
                <td className="mono">
                  <Link href={`/boms/${b.id}`}>{b.code}</Link>
                  {b.revision > 1 && <span className="muted"> rev {b.revision}</span>}
                </td>
                <td>{b.title ?? ""}</td>
                <td>{b.site}</td>
                <td className="num">{b.lines}</td>
                <td className="status">{statusTag(b.status)}</td>
                <td>{formatIST(b.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

export default function Home() {
  return (
    <Guard need="builder">
      <Dashboard />
    </Guard>
  );
}
