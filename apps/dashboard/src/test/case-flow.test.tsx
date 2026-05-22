/**
 * Slice 15 — alert-to-case-to-PDF flow.
 *
 * Walks the staff happy path end-to-end with fake HTTP:
 *   1. Doctor lands on /alerts — one alert is in the feed.
 *   2. Clicking the alert routes to /alerts/:caseId.
 *   3. The case detail renders patient context + lifecycle actions.
 *   4. Generating a handover surfaces the signed link.
 *
 * The HandoverPanel calls `POST /emergency/alerts/:id/handover` with
 * destination + registration #; we assert the request body and the
 * post-success "Open signed PDF" anchor.
 */

import { describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderApp, type Route } from "./helpers";

const CASE_ID = "11111111-1111-4111-8111-111111111111";
const RESIDENT_ID = "22222222-2222-4222-8222-222222222222";

function doctorRoutes(extra: Route[] = []): Route[] {
  return [
    {
      match: "GET http://api.test/api/v1/auth/csrf",
      respond: () => ({ status: 200, json: { csrf_token: "csrf-handover" } }),
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
    ...extra,
  ];
}

const baseAlert = {
  id: CASE_ID,
  project_id: "p-1",
  resident_id: RESIDENT_ID,
  resident_name: "Asha Rao",
  flat_villa_number: "B-1203",
  status: "alerted" as const,
  alert_time: "2026-05-22T10:00:00Z",
  acknowledged_at: null,
  en_route_at: null,
  on_site_at: null,
  escalated_at: null,
  closed_at: null,
  symptom_codes: ["chest_pain"],
  location_text: null,
  assigned_doctor_id: null,
  resolved_outcome: null,
  notification_attempts: [
    {
      channel: "fcm",
      recipient_id: "u-1",
      status: "sent",
      provider_ref: "projects/demo/messages/abc",
      error: null,
    },
  ],
};

describe("alert-to-case-to-handover flow", () => {
  it("lists alerts, opens a case, and generates a handover", async () => {
    const handoverBodies: unknown[] = [];
    const handoverRequests: RequestInit[] = [];

    renderApp({
      routes: doctorRoutes([
        {
          match: "GET http://api.test/api/v1/emergency/alerts/active",
          respond: () => ({ status: 200, json: [baseAlert] }),
        },
        {
          match: `GET http://api.test/api/v1/emergency/alerts/${CASE_ID}`,
          respond: () => ({
            status: 200,
            json: { ...baseAlert, vitals: [], notes: [], events: [] },
          }),
        },
        {
          match: `POST http://api.test/api/v1/emergency/alerts/${CASE_ID}/handover`,
          capture: (body) => handoverBodies.push(body),
          captureRequest: (init) => {
            if (init) handoverRequests.push(init);
          },
          respond: () => ({
            status: 200,
            json: {
              id: "h-1",
              case_id: CASE_ID,
              hospital_destination: "Apollo Hospital",
              doctor_registration_number: "MCI-12345",
              signed_url: "https://signed.example/handover/abc.pdf",
            },
          }),
        },
      ]),
      initialPath: "/alerts",
    });

    // (1) Alert feed.
    const alertLink = await screen.findByText(/Asha Rao · B-1203/);
    fireEvent.click(alertLink);

    // (2) Case detail loads — patient context appears.
    await waitFor(() => {
      expect(screen.getByText(/View patient profile/)).toBeInTheDocument();
    });

    // (3) Generate handover.
    const user = userEvent.setup();
    const destination = screen.getByLabelText(/hospital destination/i);
    const registration = screen.getByLabelText(/doctor registration/i);
    await user.type(destination, "Apollo Hospital");
    await user.type(registration, "MCI-12345");
    const submit = screen.getByRole("button", { name: /generate handover/i });
    await user.click(submit);

    // (4) Signed link appears.
    const link = await screen.findByRole("link", { name: /open signed pdf/i });
    expect(link).toHaveAttribute("href", "https://signed.example/handover/abc.pdf");
    expect(handoverBodies).toHaveLength(1);
    expect(handoverBodies[0]).toMatchObject({
      hospital_destination: "Apollo Hospital",
      doctor_registration_number: "MCI-12345",
    });
    expect(handoverRequests).toHaveLength(1);
    const handoverRequest = handoverRequests[0]!;
    expect(new Headers(handoverRequest.headers).get("X-CSRF-Token")).toBe(
      "csrf-handover",
    );
    expect(handoverRequest.credentials).toBe("include");
  });
});
