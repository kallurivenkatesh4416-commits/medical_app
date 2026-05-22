/**
 * Slice 15 — full staff patient context (Slice 3/4/9 backend surfaces).
 *
 * Brings the patient profile, medical history, records, current
 * medicines + adherence, and family contacts into one screen so the
 * doctor reading an active alert has everything the brief promises in
 * §3 "Doctor dashboard" → "Patient profile view".
 *
 * All surfaces are PHI — the RouteGuard requires a clinical role; the
 * backend enforces `forbid_phi_roles` on each endpoint as the actual
 * security boundary.
 */

import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import { staffApi } from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../components/ui/card";
import { EMPTY_MEDICINES, EMPTY_RECORDS } from "../safety";

export function PatientProfilePage() {
  const { residentId = "" } = useParams<{ residentId: string }>();
  const { client } = useAuth();

  const profile = useQuery({
    queryKey: ["residents", residentId, "profile"],
    queryFn: () => staffApi.getProfile(client, residentId),
    enabled: Boolean(residentId),
  });
  const records = useQuery({
    queryKey: ["residents", residentId, "records"],
    queryFn: () => staffApi.listRecords(client, residentId),
    enabled: Boolean(residentId),
  });
  const schedules = useQuery({
    queryKey: ["residents", residentId, "medicines"],
    queryFn: () => staffApi.listSchedules(client, residentId),
    enabled: Boolean(residentId),
  });
  const adherence = useQuery({
    queryKey: ["residents", residentId, "adherence", 7],
    queryFn: () => staffApi.adherence(client, residentId, 7),
    enabled: Boolean(residentId),
  });

  const openRecord = useMutation({
    mutationFn: async (recordId: string) => {
      const { url } = await staffApi.recordLink(client, residentId, recordId);
      return url;
    },
    onSuccess: (url) => {
      window.open(url, "_blank", "noopener,noreferrer");
    },
  });

  if (profile.isLoading) {
    return <p className="text-muted-foreground">Loading patient context…</p>;
  }
  if (profile.error || !profile.data) {
    return (
      <p className="text-destructive" role="alert">
        Could not load this patient.
      </p>
    );
  }

  const p = profile.data;
  const primary = p.emergency_contacts.find((c) => c.is_primary)
    ?? p.emergency_contacts[0];
  const others = p.emergency_contacts.filter((c) => c !== primary);

  return (
    <div className="grid gap-6">
      <Link
        to="/alerts"
        className="text-sm text-primary underline-offset-4 hover:underline"
      >
        ← Back to alerts
      </Link>
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>{p.full_name ?? "Resident"}</CardTitle>
            <CardDescription>
              Flat {p.flat_villa_number} ·{" "}
              {p.dob ? `DOB ${p.dob}` : "DOB unknown"} ·{" "}
              {p.gender ?? "gender prefer-not-to-say"}
              {p.medical_profile?.blood_group
                ? ` · Blood ${p.medical_profile.blood_group}`
                : ""}
            </CardDescription>
          </div>
          {p.medical_profile?.preferred_hospital ? (
            <Badge variant="secondary">
              Prefers {p.medical_profile.preferred_hospital}
            </Badge>
          ) : null}
        </CardHeader>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Medical history</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm">
            <ListBlock label="Diseases" items={p.medical_profile?.diseases ?? []} />
            <ListBlock label="Allergies" items={p.medical_profile?.allergies ?? []} />
            <ListBlock label="Surgeries" items={p.medical_profile?.surgeries ?? []} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Emergency contacts</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 text-sm">
            {primary ? (
              <div>
                <Badge variant="default">Primary</Badge>{" "}
                <span className="font-medium">{primary.name}</span> ·{" "}
                <span className="text-muted-foreground">{primary.phone}</span>
                {primary.relation ? ` · ${primary.relation}` : ""}
              </div>
            ) : (
              <p className="text-muted-foreground">No contacts on file.</p>
            )}
            {others.map((c) => (
              <div key={`${c.name}-${c.phone}`}>
                <span className="font-medium">{c.name}</span> ·{" "}
                <span className="text-muted-foreground">{c.phone}</span>
                {c.relation ? ` · ${c.relation}` : ""}
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Medical records</CardTitle>
          <CardDescription>
            Each record opens in a new tab via a 15-minute signed link
            (Slice 4 invariant).
          </CardDescription>
        </CardHeader>
        <CardContent>
          {records.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading records…</p>
          ) : (records.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">{EMPTY_RECORDS}</p>
          ) : (
            <ul className="grid gap-2 text-sm">
              {(records.data ?? []).map((r) => (
                <li
                  key={r.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded border p-2"
                >
                  <div>
                    <div className="font-medium">{r.file_name}</div>
                    <div className="text-xs text-muted-foreground">
                      {r.record_type}
                      {r.record_date ? ` · ${r.record_date}` : ""}
                      {r.source ? ` · ${r.source}` : ""}
                    </div>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => openRecord.mutate(r.id)}
                    disabled={openRecord.isPending}
                  >
                    {openRecord.isPending ? "Opening…" : "Open"}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Current medicines</CardTitle>
          <CardDescription>
            Active schedules + 7-day adherence (consent-gated by{" "}
            <code>medicine_reminder_notifications</code>).
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 text-sm">
          {schedules.isLoading ? (
            <p className="text-muted-foreground">Loading schedules…</p>
          ) : (schedules.data ?? []).length === 0 ? (
            <p className="text-muted-foreground">{EMPTY_MEDICINES}</p>
          ) : (
            <ul className="grid gap-2">
              {(schedules.data ?? []).map((s) => (
                <li key={s.id} className="rounded border p-2">
                  <div className="font-medium">{s.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {s.dose ? `${s.dose} · ` : ""}
                    {s.frequency.replaceAll("_", " ")}
                    {s.times_of_day.length ? ` · ${s.times_of_day.join(", ")}` : ""}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {adherence.data ? (
            <div className="rounded border p-2 text-xs text-muted-foreground">
              Adherence ({adherence.data.window_days}-day): taken{" "}
              {adherence.data.totals.taken} · skipped{" "}
              {adherence.data.totals.skipped} · missed{" "}
              {adherence.data.totals.missed} · slots{" "}
              {adherence.data.totals.scheduled_slots}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

function ListBlock({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase text-muted-foreground">
        {label}
      </div>
      {items.length === 0 ? (
        <p className="text-muted-foreground">—</p>
      ) : (
        <ul className="list-inside list-disc">
          {items.map((it) => (
            <li key={it}>{it}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
