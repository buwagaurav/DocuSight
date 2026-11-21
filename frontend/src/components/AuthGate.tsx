"use client";

import { Lock } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, SIGN_IN_EVENT, setToken } from "@/lib/api";
import { Button, Card, Spinner } from "./ui";

/** Shows the sign-in screen when the backend has an access password and this browser has no valid token.
 *  Without a password (local use), or when the backend can't be reached, the app renders as before;
 *  AppShell reports an unreachable backend itself. */
export default function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "open" | "signin">("checking");
  const [session, setSession] = useState(0);   // remounts the app after signing in so it reloads its data
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.authStatus()
      .then((s) => setState(s.required && !s.signed_in ? "signin" : "open"))
      .catch(() => setState("open"));
    const onSignIn = () => setState("signin");
    window.addEventListener(SIGN_IN_EVENT, onSignIn);
    return () => window.removeEventListener(SIGN_IN_EVENT, onSignIn);
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const { token } = await api.login(password);
      setToken(token);
      setPassword("");
      setSession((n) => n + 1);
      setState("open");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (state === "checking") {
    return <div className="flex min-h-screen items-center justify-center bg-bg"><Spinner className="h-6 w-6 text-accent" /></div>;
  }
  if (state === "signin") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg px-4">
        <Card className="w-full max-w-sm p-6">
          <form onSubmit={submit} className="space-y-4">
            <div className="flex items-center gap-2">
              <Lock className="h-5 w-5 text-accent" />
              <h1 className="text-lg font-semibold text-ink">Sign in to DocuSight</h1>
            </div>
            <p className="text-sm text-ink-2">This workspace is protected. Enter the access password.</p>
            <input type="password" autoFocus required aria-label="Access password" autoComplete="current-password"
              value={password} onChange={(event) => setPassword(event.target.value)}
              className="w-full rounded-lg border border-line bg-panel px-3 py-2 text-sm text-ink" />
            {error && <p role="alert" className="text-sm text-bad">{error}</p>}
            <Button type="submit" variant="primary" className="w-full" disabled={busy || !password}>
              {busy ? <><Spinner className="h-4 w-4" /> Signing in…</> : "Sign in"}
            </Button>
          </form>
        </Card>
      </div>
    );
  }
  return <div key={session} className="contents">{children}</div>;
}
