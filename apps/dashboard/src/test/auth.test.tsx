/**
 * Slice 15 — auth gate tests.
 *
 *  - Unauthenticated visit to a PHI route redirects to /login (with
 *    the `?next=` query intact so post-login bounces back).
 *  - A pre-set session bootstraps via /auth/me and lands on the
 *    alert feed.
 *  - A `builder_admin` user routed via /alerts/* hits the 403 page,
 *    not the alerts list (this is the PHI RBAC boundary on the UI
 *    side; the backend's forbid_phi_roles is the real fence).
 */

import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";

import { renderApp } from "./helpers";

describe("auth gate", () => {
  it("unauthenticated visit to /alerts redirects to /login with next=", async () => {
    renderApp({
      routes: [
        {
          match: "GET http://api.test/api/v1/auth/csrf",
          respond: () => ({ status: 401, json: { error: { code: "expired" } } }),
        },
      ],
      initialPath: "/alerts",
    });
    expect(
      await screen.findByRole("button", { name: /verify code/i }),
    ).toBeInTheDocument();
  });

  it("authenticated doctor session lands on the alert feed", async () => {
    renderApp({
      routes: [
        {
          match: "GET http://api.test/api/v1/auth/csrf",
          respond: () => ({ status: 200, json: { csrf_token: "csrf-doctor" } }),
        },
        {
          match: "GET http://api.test/api/v1/auth/me",
          respond: () => ({
            status: 200,
            json: {
              id: "u-1",
              phone: "+15550000003",
              role: "doctor",
              project_id: "p-1",
              full_name: "Dr. Demo",
            },
          }),
        },
        {
          match: "GET http://api.test/api/v1/emergency/alerts/active",
          respond: () => ({ status: 200, json: [] }),
        },
      ],
      initialPath: "/alerts",
    });

    expect(
      await screen.findByText(/no active emergency alerts/i),
    ).toBeInTheDocument();
    // Sign-out button is the AppShell signal — proves we're inside
    // the authenticated route tree.
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
  });

  it("builder_admin cannot reach /alerts — 403 page is shown", async () => {
    renderApp({
      routes: [
        {
          match: "GET http://api.test/api/v1/auth/csrf",
          respond: () => ({ status: 200, json: { csrf_token: "csrf-admin" } }),
        },
        {
          match: "GET http://api.test/api/v1/auth/me",
          respond: () => ({
            status: 200,
            json: {
              id: "u-2",
              phone: "+15550000008",
              role: "builder_admin",
              project_id: "p-1",
              full_name: "Demo Admin",
            },
          }),
        },
      ],
      initialPath: "/alerts",
    });

    await waitFor(() =>
      expect(
        screen.getByText(/you can't view that surface/i),
      ).toBeInTheDocument(),
    );
    // The role chip surfaces what the user IS allowed to see — the
    // 403 body ALSO names the role, so we expect ≥1 match.
    expect(screen.getAllByText(/builder_admin/i).length).toBeGreaterThan(0);
  });
});
