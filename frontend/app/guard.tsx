"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { homeFor, useSession } from "@/lib/session";

type Need = "builder" | "vendor" | "admin";

function matches(need: Need, kind: string, role?: string): boolean {
  if (need === "vendor") return kind === "vendor";
  if (need === "admin") return role === "admin";
  return kind === "user" && role !== "admin";
}

/** Renders children only for the right kind of signed-in account. */
export default function Guard({ need, children }: { need: Need; children: React.ReactNode }) {
  const { me, loading } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !me) router.replace("/login");
  }, [loading, me, router]);

  if (loading || !me) return <p className="muted">Loading...</p>;
  if (!matches(need, me.kind, me.kind === "user" ? me.role : undefined)) {
    return (
      <div className="box">
        This page is not available for your account. <Link href={homeFor(me)}>Go to your home page</Link>
      </div>
    );
  }
  return <>{children}</>;
}
