"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { homeFor, ROLE_LABEL, useSession, type Me } from "@/lib/session";

type DemoAccount = { phone: string; name: string; role: string; org: string };

export default function LoginPage() {
  const { refresh, health } = useSession();
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"phone" | "code">("phone");
  const [demoOtp, setDemoOtp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [accounts, setAccounts] = useState<DemoAccount[]>([]);

  useEffect(() => {
    api<DemoAccount[]>("/auth/demo-accounts")
      .then(setAccounts)
      .catch(() => setAccounts([]));
  }, []);

  async function sendCode(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const r = await api<{ sent: boolean; demo_otp?: string }>("/auth/otp/request", { json: { phone } });
      setDemoOtp(r.demo_otp ?? null);
      setCode("");
      setStep("code");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send the code");
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api("/auth/otp/verify", { json: { phone, code } });
      await refresh();
      const me = await api<Me>("/auth/me");
      router.push(homeFor(me));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Invalid or expired code");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Sign in</h1>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {step === "phone" ? (
        <form onSubmit={sendCode}>
          <label htmlFor="phone">Phone number</label>
          <input
            id="phone"
            type="tel"
            autoComplete="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+91 90000 10001"
            required
          />{" "}
          <button type="submit" disabled={busy}>
            Send code
          </button>
        </form>
      ) : (
        <form onSubmit={verify}>
          {demoOtp && (
            <div className="box">
              <span className="sim">Simulated</span> Demo OTP for {phone}: <strong className="mono">{demoOtp}</strong>
              <div className="small muted">In production this code is sent as a WhatsApp authentication message.</div>
            </div>
          )}
          {!demoOtp && health?.demo_mode && (
            <p className="small muted">If this number is registered, a code has been sent.</p>
          )}
          <label htmlFor="code">6-digit code</label>
          <input
            id="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
          />{" "}
          <button type="submit" disabled={busy}>
            Verify
          </button>{" "}
          <button type="button" className="secondary" onClick={() => setStep("phone")}>
            Use a different number
          </button>
        </form>
      )}

      {accounts.length > 0 && (
        <>
          <h2>Demo accounts</h2>
          <p className="small muted">Fictional people and companies. Click a number to use it.</p>
          <table>
            <thead>
              <tr>
                <th>Phone</th>
                <th>Name</th>
                <th>Role</th>
                <th>Organisation</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.phone}>
                  <td className="mono">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        setPhone(a.phone);
                        setStep("phone");
                      }}
                    >
                      {a.phone}
                    </button>
                  </td>
                  <td>{a.name}</td>
                  <td>{ROLE_LABEL[a.role] ?? a.role}</td>
                  <td>{a.org}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
