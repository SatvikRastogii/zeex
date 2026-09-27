"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError } from "./api";

export type Me =
  | { kind: "vendor"; id: string; name: string }
  | { kind: "user"; id: string; name: string; role: string; org: { id: string; name: string } | null };

export type Health = { demo_mode: boolean; llm: string; clock: string };

type SessionState = {
  me: Me | null;
  health: Health | null;
  loading: boolean;
  apiDown: boolean;
  refresh: () => Promise<void>;
};

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiDown, setApiDown] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setHealth(await api<Health>("/health"));
      setApiDown(false);
    } catch {
      setApiDown(true);
    }
    try {
      setMe(await api<Me>("/auth/me"));
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) setApiDown(true);
      setMe(null);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    // Initial load; setState happens after the awaits, not synchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  return (
    <SessionContext.Provider value={{ me, health, loading, apiDown, refresh }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession outside SessionProvider");
  return ctx;
}

export const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  purchase_manager: "Purchase manager",
  site_engineer: "Site engineer",
  admin: "Platform admin",
  vendor: "Vendor",
};

export function homeFor(me: Me): string {
  if (me.kind === "vendor") return "/vendor";
  if (me.role === "admin") return "/admin";
  return "/";
}
