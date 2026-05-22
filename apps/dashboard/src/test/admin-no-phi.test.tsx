/**
 * Slice 15 — builder_admin nav stays PHI-free.
 *
 *  - The KPI page renders aggregate numbers only — no resident
 *    name / flat / case id / record id / medicine name / signed
 *    link appears in the DOM. (Backend Slice 10 enforces this on
 *    the wire; the test catches accidental cross-wiring of PHI
 *    fields into the admin page.)
 *  - The AppShell does NOT expose "Alerts" in the nav for the
 *    builder_admin role. Direct URL navigation to /alerts still
 *    bounces to /403 (covered in auth.test.tsx).
 */

import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";

import { renderApp } from "./helpers";

const PHI_TERMS = [
  // Anything that looks like patient context. If a refactor accidentally
  // pulls /residents/:id/profile into the admin queryset, one of these
  // would surface in the DOM.
  /asha rao/i,
  /B-1203/,
  /flat /i,
  /signed pdf/i,
];

describe("builder_admin dashboard is PHI-free", () => {
  it("KPI page renders aggregate counts only", async () => {
    renderApp({
      routes: [
        {
          match: "POST http://api.test/api/v1/auth/refresh",
          respond: () => ({
            status: 200,
            json: { access_token: "a-new", refresh_token: "r-new" },
          }),
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
        {
          match: "GET http://api.test/api/v1/admin/kpis",
          respond: () => ({
            status: 200,
            json: {
              project_id: "p-1",
              window_label: "Last 30 days",
              window_start: "2026-04-22T00:00:00Z",
              window_end: "2026-05-22T00:00:00Z",
              emergency: {
                total_cases: 42,
                active_cases: 3,
                closed_cases: 39,
                average_ack_seconds: 18,
                average_on_site_seconds: 240,
              },
              residents: { onboarded: 100 },
              records: { uploaded: 250 },
              medicines: {
                consented_residents: 80,
                consent_paused_residents: 5,
                scheduled_taken: 600,
                scheduled_skipped: 30,
                missed: 10,
                scheduled_slots: 640,
                prn_taken: 12,
                prn_skipped: 1,
                scheduled_adherence_percent: 93,
              },
            },
          }),
        },
      ],
      initialPath: "/admin/kpis",
      preAuth: { access: "a-1", refresh: "r-1" },
    });

    await waitFor(() =>
      expect(screen.getByText("42")).toBeInTheDocument(),
    );

    // Nav must NOT advertise PHI surfaces to this role.
    expect(
      screen.queryByRole("link", { name: /^alerts$/i }),
    ).not.toBeInTheDocument();

    // None of the canonical PHI-shaped strings should leak.
    const dom = document.body.textContent ?? "";
    for (const term of PHI_TERMS) {
      expect(dom).not.toMatch(term);
    }
  });
});
