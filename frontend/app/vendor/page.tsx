"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { formatIST, newRef } from "@/lib/format";
import Guard from "../guard";
import styles from "./inbox.module.css";
import QuoteForm from "./quote-form";
import VendorWorkOrders from "./work-orders";

type Conversation = { key: string; builder: string; rfq_code: string | null; count: number; last_body: string; last_at: string };
type Msg = {
  id: string;
  direction: "in" | "out";
  body: string;
  payload: { buttons?: string[]; form?: { type: string; rfq_id: string }; button?: string; filename?: string };
  template: string | null;
  sent_at: string;
};
type Thread = { key: string; opted_out: boolean; messages: Msg[] };
type Sample = { name: string; label: string; filename: string };

const POLL_MS = 3000;

function Inbox() {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [formFor, setFormFor] = useState<string | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [sample, setSample] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const rfqId = active && active !== "general" ? active : null;

  useEffect(() => {
    api<Sample[]>("/vendor/samples")
      .then(setSamples)
      .catch(() => setSamples([]));
  }, []);

  const load = useCallback(async () => {
    try {
      const list = await api<Conversation[]>("/vendor/conversations");
      setConvs(list);
      const key = active ?? list[0]?.key ?? null;
      if (key && !active) setActive(key);
      if (key) setThread(await api<Thread>(`/vendor/conversations/${key}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load messages");
    }
  }, [active]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [thread?.messages.length]);

  async function send(body: { text?: string; button?: string; reply_to?: string }) {
    setError(null);
    try {
      await api("/vendor/messages", { json: { client_message_id: newRef(), rfq_id: rfqId, ...body } });
      setText("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Send failed");
    }
  }

  const rfqParam = rfqId ? `&rfq_id=${rfqId}` : "";

  async function sendFile(file: File) {
    setError(null);
    try {
      await api(`/vendor/files?filename=${encodeURIComponent(file.name)}&client_message_id=${newRef()}${rfqParam}`, { body: file });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    }
  }

  async function sendSample() {
    if (!sample) return;
    setError(null);
    try {
      await api(`/vendor/samples/${sample}?client_message_id=${newRef()}${rfqParam}`, { method: "POST" });
      setSample("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Send failed");
    }
  }

  function tap(m: Msg, b: string) {
    if (b === "Submit quote" && m.payload.form) setFormFor(m.payload.form.rfq_id);
    else void send({ button: b, reply_to: m.id });
  }

  return (
    <>
      <h1>
        Simulated WhatsApp · Vendor view <span className="sim">Simulated</span>
      </h1>
      <p className="small muted">
        In production vendors use WhatsApp only. Reply STOP to stop all messages, START to resume.
      </p>
      {error && <p className="error">{error}</p>}
      <VendorWorkOrders />
      <div className={styles.inbox}>
        <nav className={styles.list} aria-label="Conversations">
          {convs.length === 0 && <p className="small muted">No messages yet.</p>}
          {convs.map((c) => (
            <button
              key={c.key}
              type="button"
              className={`${styles.conv} ${c.key === active ? styles.activeConv : ""}`}
              onClick={() => setActive(c.key)}
              aria-current={c.key === active}
            >
              <strong>{c.builder}</strong>
              <span className="mono small">{c.rfq_code ?? "General"}</span>
              <span className="small muted">{c.last_body}</span>
            </button>
          ))}
        </nav>
        <section className={styles.chat} aria-label="Messages">
          {thread?.opted_out && <p className="status">[OPTED OUT] You will not receive new messages. Send START to resume.</p>}
          <div className={styles.messages}>
            {thread?.messages.map((m) => (
              <div key={m.id} className={m.direction === "in" ? styles.mine : styles.theirs}>
                <div className={styles.bubble}>
                  {m.template && <div className="small muted mono">template: {m.template}</div>}
                  {m.payload.filename && <div className="mono small">[file] {m.payload.filename}</div>}
                  <div className={styles.body}>{m.body}</div>
                  {m.direction === "out" && m.payload.buttons && (
                    <div className={styles.buttons}>
                      {m.payload.buttons.map((b) => (
                        <button key={b} type="button" className="secondary" onClick={() => tap(m, b)}>
                          {b}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="small muted">{formatIST(m.sent_at)}</div>
                </div>
              </div>
            ))}
            <div ref={bottom} />
          </div>
          {formFor && (
            <QuoteForm
              rfqId={formFor}
              onDone={() => {
                setFormFor(null);
                void load();
              }}
            />
          )}
          <div className={styles.compose}>
            <label htmlFor="attach" className="small">
              Attach PDF/photo
            </label>
            <input id="attach" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" onChange={(e) => e.target.files?.[0] && void sendFile(e.target.files[0])} />
            {samples.length > 0 && (
              <>
                <label htmlFor="sample" className="small">
                  <span className="sim">Simulated</span> sample
                </label>
                <select id="sample" value={sample} onChange={(e) => setSample(e.target.value)}>
                  <option value="">Choose a sample document...</option>
                  {samples.map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.label}
                    </option>
                  ))}
                </select>
                <button type="button" className="secondary" disabled={!sample} onClick={sendSample}>
                  Send sample
                </button>
              </>
            )}
          </div>
          <form
            className={styles.compose}
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim()) void send({ text });
            }}
          >
            <label htmlFor="reply" className="small">
              Reply
            </label>
            <input id="reply" value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a message" />
            <button type="submit" disabled={!text.trim()}>
              Send
            </button>
          </form>
        </section>
      </div>
    </>
  );
}

export default function VendorInbox() {
  return (
    <Guard need="vendor">
      <Inbox />
    </Guard>
  );
}
