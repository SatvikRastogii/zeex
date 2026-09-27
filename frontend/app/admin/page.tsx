"use client";

import Guard from "../guard";

export default function AdminPanel() {
  return (
    <Guard need="admin">
      <h1>Demo Control Panel</h1>
      <p className="muted">Clock controls, personas and scenarios arrive in Stages 6 and 12.</p>
    </Guard>
  );
}
