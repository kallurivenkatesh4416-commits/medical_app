/**
 * Slice 15 — auth context.
 *
 * Owns:
 *   - The single `ApiClient` instance.
 *   - The current `User` (null when unauthenticated).
 *   - Bootstrap state — on first mount we attempt silent refresh via
 *     the sessionStorage token; until that resolves the rest of the
 *     app stays behind a loading splash so we never paint a "logged
 *     out" UI for a user who actually has a valid session.
 *
 * The provider value is intentionally narrow: `client`, `user`,
 * `status`, and a handful of imperative actions (`login`, `logout`).
 * Pages talk to the backend through the `client` (typed via
 * `endpoints.ts`); they never reach into AuthContext for tokens.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { ApiClient } from "../api/client";
import { authApi, type User } from "../api/auth";

type Status = "bootstrapping" | "anonymous" | "authenticated";

type AuthContextValue = {
  client: ApiClient;
  user: User | null;
  status: Status;
  /** Two-step OTP — request, then verify. Returns the verified User. */
  requestOtp: (phone: string) => Promise<{ devOtp: string | null }>;
  verifyOtp: (phone: string, code: string) => Promise<User>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be called inside <AuthProvider>");
  }
  return value;
}

/**
 * Production builds construct one `ApiClient` and pass it to
 * `<AuthProvider client={...}>`. Tests inject a configured client (with
 * a `fetch` stub) the same way so the auth flow is exercised end-to-end
 * without standing up a real backend.
 */
export function AuthProvider({
  client,
  children,
}: {
  client: ApiClient;
  children: React.ReactNode;
}) {
  const [status, setStatus] = useState<Status>("bootstrapping");
  const [user, setUser] = useState<User | null>(null);
  // Guard against the StrictMode double-effect re-bootstrapping.
  const bootstrapped = useRef(false);

  // -------- bootstrap (silent refresh on reload) -----------------------
  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    let cancelled = false;
    void (async () => {
      const restored = await client.tryRestoreSession();
      if (cancelled) return;
      if (!restored) {
        setStatus("anonymous");
        return;
      }
      try {
        const me = await authApi.me(client);
        if (cancelled) return;
        setUser(me);
        setStatus("authenticated");
      } catch {
        client.clearSession();
        setStatus("anonymous");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client]);

  // -------- session-expired toast handoff ------------------------------
  useEffect(() => {
    const unsubscribe = client.onSessionExpired(() => {
      // A refresh failed mid-session — clear the user immediately so
      // the RouteGuard bounces back to /login.
      setUser(null);
      setStatus("anonymous");
    });
    return () => {
      // Set#delete returns boolean — useEffect destructors must return
      // void, so we throw away the return.
      unsubscribe();
    };
  }, [client]);

  // -------- actions ----------------------------------------------------
  const requestOtp = useCallback<AuthContextValue["requestOtp"]>(
    async (phone) => {
      const out = await authApi.requestOtp(client, phone);
      return { devOtp: out.dev_otp };
    },
    [client],
  );

  const verifyOtp = useCallback<AuthContextValue["verifyOtp"]>(
    async (phone, code) => {
      const out = await authApi.verifyOtp(client, phone, code);
      if (out.registration_required || !out.access_token || !out.refresh_token) {
        // Slice 15 is the staff dashboard — resident registration is
        // the mobile app's path. Surface a clear error rather than
        // routing into an onboarding wizard that doesn't exist here.
        throw new Error(
          "This phone has no staff account. Use the resident app to register.",
        );
      }
      client.setSession({ access: out.access_token, refresh: out.refresh_token });
      const me = await authApi.me(client);
      setUser(me);
      setStatus("authenticated");
      return me;
    },
    [client],
  );

  const logout = useCallback<AuthContextValue["logout"]>(async () => {
    try {
      // Best-effort revoke. The client clears local storage either way.
      const raw = (client as unknown as { storageRefresh?: () => string | null })
        .storageRefresh?.();
      if (raw) await authApi.logout(client, raw);
    } catch {
      // network failures must not block local logout
    }
    client.clearSession();
    setUser(null);
    setStatus("anonymous");
  }, [client]);

  const value = useMemo<AuthContextValue>(
    () => ({ client, user, status, requestOtp, verifyOtp, logout }),
    [client, user, status, requestOtp, verifyOtp, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
