"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import Guard from "../guard";

type Job = { id: string; kind: string; run_at_display: string; attempts: number; last_error: string[] | null; payload: Record<string, unknown> };
type Event = { at_display: string; actor: string; action: string; entity: string };
type ClockOut = { display: string; jobs_ran: number; note?: string };

function Panel() {
  const { refresh } = useSession();
  const [clock, setClock] = useState<string>("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [failed, setFailed] = useState<Job[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [c, j, f, e] = await Promise.all([
      api<{ display: string }>("/admin/clock"),
      api<Job[]>("/admin/jobs"),
      api<Job[]>("/admin/jobs?status=failed"),
      api<Event[]>("/admin/events?limit=50"),
    ]);
    setClock(c.display);
    setJobs(j);
    setFailed(f);
    setEvents(e);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function move(path: string, json?: unknown) {
    setBusy(true);
    try {
      const out = await api<ClockOut>(path, { method: "POST", json });
      setNote(out.note ?? `Clock now ${out.display}. ${out.jobs_ran} job(s) ran.`);
      await Promise.all([load(), refresh()]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Demo Control Panel</h1>
      <div className="box">
        <h2>Demo clock</h2>
        <p className="mono">{clock}</p>
        <p>
          <button type="button" disabled={busy} onClick={() => move("/admin/clock/advance", { minutes: 15 })}>
            +15 min
          </button>{" "}
          <button type="button" disabled={busy} onClick={() => move("/admin/clock/advance", { minutes: 60 })}>
            +1 hour
          </button>{" "}
          <button type="button" className="secondary" disabled={busy || jobs.length === 0} onClick={() => move("/admin/clock/next-event")}>
            To next event
          </button>
        </p>
        {note && <p className="small">{note}</p>}
        <p className="small muted">Moving the clock runs every job that becomes due, in order. The clock never goes back; use Reset (Stage 12) to start over.</p>
      </div>

      <h2>Pending jobs ({jobs.length})</h2>
      <table>
        <thead>
          <tr>
            <th>Runs at</th>
            <th>Kind</th>
            <th className="num">Attempts</th>
            <th>Last error</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id}>
              <td className="mono">{j.run_at_display}</td>
              <td className="mono">{j.kind}</td>
              <td className="num">{j.attempts}</td>
              <td className="small">{j.last_error?.join(" ") ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {failed.length > 0 && (
        <>
          <h2>Failed jobs ({failed.length})</h2>
          <table>
            <tbody>
              {failed.map((j) => (
                <tr key={j.id}>
                  <td className="mono">{j.kind}</td>
                  <td className="small">{j.last_error?.join(" ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2>Event log</h2>
      <table>
        <thead>
          <tr>
            <th>At</th>
            <th>Actor</th>
            <th>Action</th>
            <th>Entity</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e, i) => (
            <tr key={i}>
              <td className="mono">{e.at_display}</td>
              <td className="small mono">{e.actor}</td>
              <td className="mono">{e.action}</td>
              <td>{e.entity}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export default function AdminPanel() {
  return (
    <Guard need="admin">
      <Panel />
    </Guard>
  );
}
