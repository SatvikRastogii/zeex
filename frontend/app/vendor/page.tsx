"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { formatIST, newRef } from "@/lib/format";
import Guard from "../guard";
import styles from "./inbox.module.css";

type Conversation = { key: string; builder: string; rfq_code: string | null; count: number; last_body: string; last_at: string };
type Msg = {
  id: string;
  direction: "in" | "out";
  body: string;
  payload: { buttons?: string[]; form?: { type: string }; button?: string };
  template: string | null;
  sent_at: string;
};
type Thread = { key: string; opted_out: boolean; messages: Msg[] };

const POLL_MS = 3000;

function Inbox() {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

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

  async function send(body: { text?: string; button?: string }) {
    setError(null);
    try {
      await api("/vendor/messages", {
        json: { client_message_id: newRef(), rfq_id: active && active !== "general" ? active : null, ...body },
      });
      setText("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Send failed");
    }
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
                  <div className={styles.body}>{m.body}</div>
                  {m.payload.form?.type === "quote_form" && (
                    <div className="box small">Quote form: submit your rate from the quote form (Stage 7), or reply with text.</div>
                  )}
                  {m.direction === "out" && m.payload.buttons && (
                    <div className={styles.buttons}>
                      {m.payload.buttons.map((b) => (
                        <button key={b} type="button" className="secondary" onClick={() => send({ button: b })}>
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
