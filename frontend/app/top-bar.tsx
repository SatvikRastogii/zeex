"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type Health = { demo_mode: boolean; llm: string };

export default function TopBar() {
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setHealth)
      .catch(() => setDown(true));
  }, []);

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link className="app" href="/">Z-Procure</Link>
        <span className="spacer" />
        <span className="muted">Not signed in</span>
        {down && <span className="status">[API UNREACHABLE]</span>}
        {health?.demo_mode && <span className="mono">DEMO</span>}
        {health?.llm === "mock" && <span className="sim">LLM: Simulated (mock)</span>}
      </div>
    </header>
  );
}
