"use client";

import Guard from "./guard";

export default function Home() {
  return (
    <Guard need="builder">
      <h1>Dashboard</h1>
      <p className="muted">Open BOMs and items needing action appear here from Stage 4.</p>
    </Guard>
  );
}
