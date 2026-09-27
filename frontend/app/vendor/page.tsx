"use client";

import Guard from "../guard";

export default function VendorInbox() {
  return (
    <Guard need="vendor">
      <h1>Simulated WhatsApp · Vendor view</h1>
      <p className="muted">Conversations appear here from Stage 6.</p>
    </Guard>
  );
}
