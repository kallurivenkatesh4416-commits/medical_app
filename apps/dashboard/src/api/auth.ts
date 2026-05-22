/**
 * Slice 15 — auth endpoints (two-step OTP + me/refresh/logout).
 *
 * Same backend shape Slice 14 mobile uses. The `dev_otp` field on
 * `/auth/otp/request` is non-null only when `APP_ENV=local`; the
 * dashboard surfaces it as a "Dev OTP: 123456" hint so the local demo
 * does not need an actual SMS. Production never exposes it.
 */

import type { ApiClient } from "./client";

export type Role =
  | "resident"
  | "family"
  | "doctor"
  | "nurse"
  | "ops"
  | "hospital"
  | "security_desk"
  | "builder_admin"
  | "super_admin";

/** Roles whose dashboards can read PHI surfaces (alerts / cases / profiles). */
export const PHI_ROLES: ReadonlyArray<Role> = ["doctor", "nurse", "ops"];

/** Roles allowed on the admin surface — aggregate counts only. */
export const ADMIN_ROLES: ReadonlyArray<Role> = ["builder_admin"];

export type User = {
  id: string;
  phone: string;
  role: Role;
  project_id: string | null;
  full_name: string | null;
};

export type OtpRequestOut = {
  sent: boolean;
  dev_otp: string | null;
};

export type OtpVerifyOut = {
  registration_required: boolean;
  access_token: string | null;
  refresh_token: string | null;
  registration_token: string | null;
  session_transport: "bearer" | "cookie";
};

export const authApi = {
  async requestOtp(client: ApiClient, phone: string): Promise<OtpRequestOut> {
    return client.post<OtpRequestOut>("/api/v1/auth/otp/request", { phone });
  },

  async verifyOtp(client: ApiClient, phone: string, code: string): Promise<OtpVerifyOut> {
    return client.post<OtpVerifyOut>("/api/v1/auth/otp/verify", {
      phone,
      code,
      dashboard_session: true,
    });
  },

  async me(client: ApiClient): Promise<User> {
    return client.get<User>("/api/v1/auth/me");
  },

  async logout(client: ApiClient): Promise<void> {
    // The /auth/logout response is 204 — `request` returns null safely.
    await client.post("/api/v1/auth/logout");
  },
};
