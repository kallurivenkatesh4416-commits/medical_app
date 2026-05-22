/**
 * Slice 15 — case detail.
 *
 * Drives the full Slice 6/7/8 doctor workflow off one screen:
 *   - Lifecycle transitions (alerted → acknowledged → en_route →
 *     on_site → treated_on_site | escalated → closed).
 *   - Vitals form (Slice 7).
 *   - Notes form with the Telemedicine fields (Slice 7).
 *   - Hospital handover PDF panel (Slice 8) — generation, signed-link
 *     open / refresh, email + WhatsApp dispatch.
 *   - Cross-link to the patient profile page (Slice 3/4/9 staff
 *     surface).
 *
 * Polling matches AlertsList — 5s — so a doctor watching a colleague's
 * actions sees them land without manual refresh.
 */

import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  emergencyApi,
  handoverApi,
  type CaseStatus,
} from "../api/endpoints";
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
import { Input } from "../components/ui/input";
import { EMPTY_NOTES, EMPTY_VITALS } from "../safety";

const POLL_MS = 5_000;

const NEXT_STATUSES: Partial<Record<CaseStatus, { value: CaseStatus; label: string }[]>> = {
  alerted: [{ value: "acknowledged", label: "Acknowledge" }],
  acknowledged: [{ value: "en_route", label: "En route" }],
  en_route: [{ value: "on_site", label: "On site" }],
  on_site: [
    { value: "treated_on_site", label: "Treated on site" },
    { value: "escalated", label: "Escalate" },
  ],
  treated_on_site: [{ value: "closed", label: "Close case" }],
  escalated: [{ value: "closed", label: "Close case" }],
  closed: [],
};

export function CaseDetailPage() {
  const { caseId = "" } = useParams<{ caseId: string }>();
  const { client } = useAuth();
  const qc = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["alerts", "detail", caseId],
    queryFn: () => emergencyApi.getDetail(client, caseId),
    refetchInterval: POLL_MS,
    enabled: Boolean(caseId),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["alerts"] });

  const transition = useMutation({
    mutationFn: (target: CaseStatus) =>
      emergencyApi.transition(client, caseId, { target_status: target }),
    onSuccess: invalidate,
  });

  if (isLoading) {
    return <p className="text-muted-foreground">Loading case…</p>;
  }
  if (error || !data) {
    return (
      <p className="text-destructive" role="alert">
        Could not load this case.
      </p>
    );
  }

  const nextActions = NEXT_STATUSES[data.status] ?? [];

  return (
    <div className="grid gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">
            {data.resident_name ?? "Resident"} · {data.flat_villa_number ?? "Flat unknown"}
          </h1>
          <p className="text-sm text-muted-foreground">
            Alerted {new Date(data.alert_time).toLocaleString()} · symptoms:{" "}
            {data.symptom_codes.length ? data.symptom_codes.join(", ") : "none selected"}
          </p>
          <Link
            to={`/residents/${data.resident_id}`}
            className="text-sm text-primary underline-offset-4 hover:underline"
          >
            View patient profile →
          </Link>
        </div>
        <Badge variant="outline">{data.status}</Badge>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Lifecycle</CardTitle>
          <CardDescription>
            Transitions follow Slice 7's state machine — backend enforces
            the allowed targets.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {nextActions.length === 0 ? (
            <span className="text-sm text-muted-foreground">No further transitions.</span>
          ) : (
            nextActions.map((step) => (
              <Button
                key={step.value}
                onClick={() => transition.mutate(step.value)}
                disabled={transition.isPending}
              >
                {transition.isPending ? "Saving…" : step.label}
              </Button>
            ))
          )}
        </CardContent>
      </Card>

      <VitalsPanel caseId={caseId} vitals={data.vitals} onSaved={invalidate} />
      <NotesPanel caseId={caseId} notes={data.notes} onSaved={invalidate} />
      <HandoverPanel caseId={caseId} initialAssessmentHint={data.symptom_codes.join(", ")} />
    </div>
  );
}

// --------------------------------------------------------------------- //
// Vitals                                                                 //
// --------------------------------------------------------------------- //

const VITAL_FIELDS: Array<{
  key:
    | "blood_pressure_systolic"
    | "blood_pressure_diastolic"
    | "spo2_percent"
    | "heart_rate_bpm"
    | "respiratory_rate_bpm"
    | "temperature_c";
  label: string;
  step?: string;
}> = [
  { key: "blood_pressure_systolic", label: "BP systolic" },
  { key: "blood_pressure_diastolic", label: "BP diastolic" },
  { key: "spo2_percent", label: "SpO₂ %" },
  { key: "heart_rate_bpm", label: "Heart rate" },
  { key: "respiratory_rate_bpm", label: "Resp. rate" },
  { key: "temperature_c", label: "Temp °C", step: "0.1" },
];

type VitalsState = Record<(typeof VITAL_FIELDS)[number]["key"], string>;

function emptyVitalsState(): VitalsState {
  return VITAL_FIELDS.reduce(
    (acc, f) => {
      acc[f.key] = "";
      return acc;
    },
    {} as VitalsState,
  );
}

function VitalsPanel({
  caseId,
  vitals,
  onSaved,
}: {
  caseId: string;
  vitals: Array<{
    id: string;
    blood_pressure_systolic: number | null;
    blood_pressure_diastolic: number | null;
    spo2_percent: number | null;
    heart_rate_bpm: number | null;
    respiratory_rate_bpm: number | null;
    temperature_c: number | null;
    recorded_at: string;
  }>;
  onSaved: () => void;
}) {
  const { client } = useAuth();
  const [draft, setDraft] = useState<VitalsState>(emptyVitalsState);
  const save = useMutation({
    mutationFn: (body: VitalsState) => {
      const payload = Object.fromEntries(
        Object.entries(body)
          .filter(([, v]) => v.trim() !== "")
          .map(([k, v]) => [k, k === "temperature_c" ? parseFloat(v) : parseInt(v, 10)]),
      );
      return emergencyApi.recordVitals(client, caseId, payload as never);
    },
    onSuccess: () => {
      setDraft(emptyVitalsState());
      onSaved();
    },
  });

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    save.mutate(draft);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Vitals</CardTitle>
        <CardDescription>Manual entry by clinical staff.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {vitals.length === 0 ? (
          <p className="text-sm text-muted-foreground">{EMPTY_VITALS}</p>
        ) : (
          <ul className="grid gap-2 text-sm">
            {vitals.map((v) => (
              <li key={v.id} className="rounded border p-2 text-muted-foreground">
                {new Date(v.recorded_at).toLocaleString()} · BP {v.blood_pressure_systolic ?? "–"}/
                {v.blood_pressure_diastolic ?? "–"} · SpO₂ {v.spo2_percent ?? "–"}% · HR{" "}
                {v.heart_rate_bpm ?? "–"} · RR {v.respiratory_rate_bpm ?? "–"} · Temp{" "}
                {v.temperature_c ?? "–"}°C
              </li>
            ))}
          </ul>
        )}
        <form
          onSubmit={onSubmit}
          className="grid grid-cols-1 gap-3 sm:grid-cols-3"
        >
          {VITAL_FIELDS.map((field) => (
            <label key={field.key} className="space-y-1 text-sm">
              <span className="font-medium">{field.label}</span>
              <Input
                inputMode="decimal"
                step={field.step}
                value={draft[field.key]}
                onChange={(e) =>
                  setDraft({ ...draft, [field.key]: e.target.value })
                }
              />
            </label>
          ))}
          <Button type="submit" className="sm:col-span-3" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save vitals"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------- //
// Notes (Telemedicine fields)                                            //
// --------------------------------------------------------------------- //

function NotesPanel({
  caseId,
  notes,
  onSaved,
}: {
  caseId: string;
  notes: Array<{
    id: string;
    note_type: string;
    body: string;
    doctor_name: string | null;
    doctor_registration_number: string | null;
    created_at: string;
  }>;
  onSaved: () => void;
}) {
  const { client } = useAuth();
  const [body, setBody] = useState("");
  const [doctorName, setDoctorName] = useState("");
  const [doctorReg, setDoctorReg] = useState("");
  const [advice, setAdvice] = useState("");
  const [consent, setConsent] = useState(false);

  const save = useMutation({
    mutationFn: () =>
      emergencyApi.recordNote(client, caseId, {
        note_type: "treatment",
        body,
        doctor_name: doctorName || null,
        doctor_registration_number: doctorReg || null,
        consultation_timestamp: new Date().toISOString(),
        advice_given: advice || null,
        patient_consent_obtained: consent,
      }),
    onSuccess: () => {
      setBody("");
      setAdvice("");
      onSaved();
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Notes</CardTitle>
        <CardDescription>
          Telemedicine-compliant — doctor name + registration + consent
          stay attached to the case.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {notes.length === 0 ? (
          <p className="text-sm text-muted-foreground">{EMPTY_NOTES}</p>
        ) : (
          <ul className="grid gap-2 text-sm">
            {notes.map((n) => (
              <li key={n.id} className="rounded border p-3">
                <div className="text-xs text-muted-foreground">
                  {n.note_type} · {new Date(n.created_at).toLocaleString()} ·{" "}
                  {n.doctor_name ?? "—"}
                  {n.doctor_registration_number ? ` (${n.doctor_registration_number})` : ""}
                </div>
                <p className="mt-1 whitespace-pre-wrap">{n.body}</p>
              </li>
            ))}
          </ul>
        )}
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <label className="space-y-1 text-sm">
            <span className="font-medium">Note</span>
            <textarea
              className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              required
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="font-medium">Doctor name</span>
              <Input value={doctorName} onChange={(e) => setDoctorName(e.target.value)} />
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Registration #</span>
              <Input value={doctorReg} onChange={(e) => setDoctorReg(e.target.value)} />
            </label>
          </div>
          <label className="space-y-1 text-sm">
            <span className="font-medium">Advice given</span>
            <textarea
              className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={advice}
              onChange={(e) => setAdvice(e.target.value)}
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
            />
            Patient consent obtained
          </label>
          <Button type="submit" disabled={save.isPending || !body.trim()}>
            {save.isPending ? "Saving…" : "Save note"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------- //
// Handover (Slice 8)                                                     //
// --------------------------------------------------------------------- //

function HandoverPanel({
  caseId,
  initialAssessmentHint,
}: {
  caseId: string;
  initialAssessmentHint: string;
}) {
  const { client } = useAuth();
  const [destination, setDestination] = useState("");
  const [doctorReg, setDoctorReg] = useState("");
  const [assessment, setAssessment] = useState(initialAssessmentHint);
  const [signedUrl, setSignedUrl] = useState<string | null>(null);
  const [handoverId, setHandoverId] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [whatsapp, setWhatsapp] = useState("");
  const [dispatchInfo, setDispatchInfo] = useState<string | null>(null);

  const generate = useMutation({
    mutationFn: () =>
      handoverApi.generate(client, caseId, {
        hospital_destination: destination,
        doctor_registration_number: doctorReg,
        doctor_assessment: assessment || null,
      }),
    onSuccess: (out) => {
      setHandoverId(out.id);
      setSignedUrl(out.signed_url);
      setDispatchInfo("Handover ready (signed link expires in 15 min).");
    },
  });

  const refreshLink = useMutation({
    mutationFn: () => {
      if (!handoverId) throw new Error("Generate the handover first.");
      return handoverApi.refreshLink(client, handoverId);
    },
    onSuccess: (out) => setSignedUrl(out.url),
  });

  const dispatch = useMutation({
    mutationFn: () => {
      if (!handoverId) throw new Error("Generate the handover first.");
      return handoverApi.dispatch(client, handoverId, {
        email: email || null,
        whatsapp: whatsapp || null,
      });
    },
    onSuccess: (out) => {
      setDispatchInfo(
        out.dispatches
          .map((d) => `${d.channel}: ${d.status}${d.error ? ` (${d.error})` : ""}`)
          .join(" · ") || "Dispatched.",
      );
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Hospital handover PDF</CardTitle>
        <CardDescription>
          Generated PDF is only shared via a 15-minute signed link.
          Hospital consent gate is enforced by the backend at generate /
          refresh / dispatch.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span className="font-medium">Hospital destination</span>
            <Input
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
              placeholder="e.g. Apollo Hospital, Sarjapur Road"
            />
          </label>
          <label className="space-y-1 text-sm">
            <span className="font-medium">Doctor registration #</span>
            <Input
              value={doctorReg}
              onChange={(e) => setDoctorReg(e.target.value)}
            />
          </label>
        </div>
        <label className="space-y-1 text-sm">
          <span className="font-medium">Refined assessment (optional)</span>
          <textarea
            className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            value={assessment}
            onChange={(e) => setAssessment(e.target.value)}
          />
        </label>
        <Button
          onClick={() => generate.mutate()}
          disabled={generate.isPending || !destination.trim() || !doctorReg.trim()}
        >
          {generate.isPending ? "Generating…" : "Generate handover PDF"}
        </Button>
        {signedUrl ? (
          <div className="flex flex-wrap items-center gap-3">
            <a
              href={signedUrl}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-primary underline-offset-4 hover:underline"
            >
              Open signed PDF
            </a>
            <Button
              variant="outline"
              size="sm"
              onClick={() => refreshLink.mutate()}
              disabled={refreshLink.isPending}
            >
              {refreshLink.isPending ? "Refreshing…" : "Refresh link"}
            </Button>
          </div>
        ) : null}
        {handoverId ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="font-medium">Hospital email</span>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="er@hospital.example"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Hospital WhatsApp</span>
              <Input
                value={whatsapp}
                onChange={(e) => setWhatsapp(e.target.value)}
                placeholder="+919xxxxxxxxx"
              />
            </label>
            <Button
              className="sm:col-span-2"
              variant="outline"
              onClick={() => dispatch.mutate()}
              disabled={dispatch.isPending || (!email && !whatsapp)}
            >
              {dispatch.isPending ? "Dispatching…" : "Send handover"}
            </Button>
          </div>
        ) : null}
        {dispatchInfo ? (
          <p className="text-sm text-muted-foreground" role="status">
            {dispatchInfo}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
