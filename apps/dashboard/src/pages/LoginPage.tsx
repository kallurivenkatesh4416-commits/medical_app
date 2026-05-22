/**
 * Slice 15 — staff OTP login.
 *
 * Two-step flow:
 *   1. Phone → POST /auth/otp/request. Backend returns `dev_otp` only
 *      when APP_ENV=local; we surface it as a hint so the local demo
 *      doesn't need a real SMS provider.
 *   2. Phone + code → POST /auth/otp/verify. On success we set tokens
 *      and route to the `?next=` query param (defaulting to /alerts).
 *
 * No bearer token paste UI — that was the old `App.tsx` shape and
 * the audit P1 #4 finding. No `localStorage` either; AuthContext
 * holds the access token in memory and stashes the refresh token in
 * `sessionStorage` per the Slice 15 scoping decision.
 */

import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../components/ui/card";

export function LoginPage() {
  const { requestOtp, verifyOtp } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [codeRequested, setCodeRequested] = useState(false);

  async function onRequest() {
    setError(null);
    setInfo(null);
    setRequesting(true);
    try {
      const { devOtp } = await requestOtp(phone.trim());
      setCodeRequested(true);
      if (devOtp) {
        setCode(devOtp);
        setInfo(`Dev OTP auto-filled: ${devOtp}`);
      } else {
        setInfo("Code sent. Check your SMS.");
      }
    } catch (e) {
      setError(asErrorMessage(e, "Could not send a code."));
    } finally {
      setRequesting(false);
    }
  }

  async function onVerify(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setVerifying(true);
    try {
      await verifyOtp(phone.trim(), code.trim());
      const next = params.get("next");
      navigate(next ? decodeURIComponent(next) : "/alerts", { replace: true });
    } catch (e) {
      setError(asErrorMessage(e, "Could not verify the code."));
    } finally {
      setVerifying(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Staff sign in</CardTitle>
          <CardDescription>
            Use your registered phone number to receive a one-time code.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={onVerify}>
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="phone">
                Phone number
              </label>
              <Input
                id="phone"
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+15550009999"
                autoComplete="tel"
                required
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void onRequest()}
                disabled={requesting || !phone.trim()}
              >
                {requesting ? "Sending…" : "Send code"}
              </Button>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium" htmlFor="code">
                One-time code
              </label>
              <Input
                id="code"
                inputMode="numeric"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="123456"
                autoComplete="one-time-code"
                required
              />
              {codeRequested ? (
                <p className="text-xs text-muted-foreground">Code sent to your phone.</p>
              ) : null}
            </div>
            {info ? (
              <p className="text-sm text-muted-foreground">{info}</p>
            ) : null}
            {error ? (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={verifying}>
              {verifying ? "Verifying…" : "Verify code"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function asErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
