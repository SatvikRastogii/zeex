"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { ROLE_LABEL, useSession } from "@/lib/session";

export default function TopBar() {
  const { me, health, apiDown, refresh } = useSession();
  const router = useRouter();

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    await refresh();
    router.push("/login");
  }

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link className="app" href="/">
          Z-Procure
        </Link>
        <span className="spacer" />
        {me?.kind === "user" && (
          <span>
            {me.name} · {ROLE_LABEL[me.role] ?? me.role}
            {me.org ? ` · ${me.org.name}` : ""}
          </span>
        )}
        {me?.kind === "vendor" && <span>{me.name} · Vendor</span>}
        {!me && <span className="muted">Not signed in</span>}
        {apiDown && <span className="status">[API UNREACHABLE]</span>}
        {health?.demo_mode && <span className="mono">DEMO · Clock: {health.clock}</span>}
        {health?.llm === "mock" && <span className="sim">LLM: Simulated (mock)</span>}
        {me && (
          <button type="button" className="secondary" onClick={logout}>
            Log out
          </button>
        )}
      </div>
    </header>
  );
}
