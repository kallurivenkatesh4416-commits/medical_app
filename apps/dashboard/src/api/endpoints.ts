/**
 * Slice 15 — typed API surface for the staff workflow.
 *
 * Each function is a thin wrapper around `ApiClient` so tests can:
 *   a) build an `ApiClient` with a custom `fetch` (HTTP-level tests), OR
 *   b) inject an `ApiClient`-shaped fake via the React context (page
 *      tests). The wrappers exist so the JSON shape lives in one place
 *      and TypeScript catches drift against the backend Pydantic models.
 *
 * No PHI ever leaks into the URL — every resource id is a UUID and
 * the bearer token is the access boundary the backend enforces.
 */

import type { ApiClient } from "./client";

// --------------------------------------------------------------------- //
// Alerts + cases                                                         //
// --------------------------------------------------------------------- //

export type NotificationAttempt = {
  channel: "fcm" | "sms" | "voice";
  recipient_id: string | null;
  status: string;
  provider_ref: string | null;
  error: string | null;
};

export type CaseStatus =
  | "alerted"
  | "acknowledged"
  | "en_route"
  | "on_site"
  | "treated_on_site"
  | "escalated"
  | "closed";

export type Alert = {
  id: string;
  project_id: string;
  resident_id: string;
  resident_name: string | null;
  flat_villa_number: string | null;
  status: CaseStatus;
  alert_time: string;
  acknowledged_at: string | null;
  en_route_at: string | null;
  on_site_at: string | null;
  escalated_at: string | null;
  closed_at: string | null;
  symptom_codes: string[];
  location_text: string | null;
  assigned_doctor_id: string | null;
  resolved_outcome: string | null;
  notification_attempts: NotificationAttempt[];
};

export type CaseVital = {
  id: string;
  case_id: string;
  project_id: string;
  recorded_by: string;
  recorded_at: string;
  blood_pressure_systolic: number | null;
  blood_pressure_diastolic: number | null;
  spo2_percent: number | null;
  heart_rate_bpm: number | null;
  respiratory_rate_bpm: number | null;
  temperature_c: number | null;
  notes: string | null;
};

export type CaseNote = {
  id: string;
  case_id: string;
  project_id: string;
  author_id: string;
  note_type: "observation" | "treatment" | "escalation_reason";
  body: string;
  doctor_name: string | null;
  doctor_registration_number: string | null;
  consultation_timestamp: string | null;
  advice_given: string | null;
  patient_consent_obtained: boolean;
  created_at: string;
};

export type CaseEvent = {
  id: string;
  case_id: string;
  actor_user_id: string | null;
  event_type: string;
  from_status: string | null;
  to_status: string | null;
  meta: Record<string, unknown>;
  created_at: string;
};

export type AlertDetail = Alert & {
  vitals: CaseVital[];
  notes: CaseNote[];
  events: CaseEvent[];
};

export const emergencyApi = {
  listActive(client: ApiClient) {
    return client.get<Alert[]>("/api/v1/emergency/alerts/active");
  },
  getDetail(client: ApiClient, caseId: string) {
    return client.get<AlertDetail>(`/api/v1/emergency/alerts/${caseId}`);
  },
  transition(
    client: ApiClient,
    caseId: string,
    body: { target_status: CaseStatus; resolved_outcome?: string | null },
  ) {
    return client.post<Alert>(`/api/v1/emergency/alerts/${caseId}/transition`, body);
  },
  recordVitals(client: ApiClient, caseId: string, body: Omit<CaseVital, "id" | "case_id" | "project_id" | "recorded_by" | "recorded_at">) {
    return client.post<CaseVital>(`/api/v1/emergency/alerts/${caseId}/vitals`, body);
  },
  recordNote(client: ApiClient, caseId: string, body: Omit<CaseNote, "id" | "case_id" | "project_id" | "author_id" | "created_at">) {
    return client.post<CaseNote>(`/api/v1/emergency/alerts/${caseId}/notes`, body);
  },
};

// --------------------------------------------------------------------- //
// Patient context (Slice 3/4/9 staff surfaces)                          //
// --------------------------------------------------------------------- //

export type EmergencyContact = {
  name: string;
  phone: string;
  relation: string | null;
  is_primary: boolean;
};

export type MedicalProfile = {
  blood_group: string | null;
  diseases: string[];
  allergies: string[];
  surgeries: string[];
  preferred_hospital: string | null;
};

export type ResidentProfile = {
  id: string;
  user_id: string;
  full_name: string | null;
  phone: string | null;
  flat_villa_number: string;
  dob: string | null;
  gender: string | null;
  medical_profile: MedicalProfile | null;
  emergency_contacts: EmergencyContact[];
};

export type MedicalRecord = {
  id: string;
  file_name: string;
  content_type: string;
  record_type: string;
  record_date: string | null;
  source: string | null;
  tags: string[];
  size_bytes: number;
  created_at: string;
};

export type MedicineSchedule = {
  id: string;
  resident_id: string;
  project_id: string;
  prescribed_by: string;
  name: string;
  dose: string | null;
  instructions: string | null;
  frequency: string;
  times_of_day: string[];
  start_date: string;
  end_date: string | null;
  active: boolean;
  created_at: string;
};

export type AdherenceTotals = {
  taken: number;
  skipped: number;
  missed: number;
  scheduled_slots: number;
};

export type AdherenceSchedule = AdherenceTotals & {
  schedule_id: string;
  name: string;
  frequency: string;
  active: boolean;
};

export type Adherence = {
  resident_id: string;
  window_days: number;
  totals: AdherenceTotals;
  schedules: AdherenceSchedule[];
};

export const staffApi = {
  getProfile(client: ApiClient, residentId: string) {
    return client.get<ResidentProfile>(`/api/v1/residents/${residentId}/profile`);
  },
  listRecords(client: ApiClient, residentId: string) {
    return client.get<MedicalRecord[]>(`/api/v1/residents/${residentId}/records`);
  },
  recordLink(client: ApiClient, residentId: string, recordId: string) {
    return client.get<{ url: string; expires_in_seconds: number }>(
      `/api/v1/residents/${residentId}/records/${recordId}/link`,
    );
  },
  listSchedules(client: ApiClient, residentId: string) {
    return client.get<MedicineSchedule[]>(
      `/api/v1/residents/${residentId}/medicines/schedules`,
    );
  },
  adherence(client: ApiClient, residentId: string, days = 7) {
    return client.get<Adherence>(
      `/api/v1/residents/${residentId}/medicines/adherence?days=${days}`,
    );
  },
};

// --------------------------------------------------------------------- //
// Handover PDF (Slice 8)                                                 //
// --------------------------------------------------------------------- //

export type HandoverOut = {
  id: string;
  case_id: string;
  hospital_destination: string;
  doctor_registration_number: string;
  signed_url: string;
};

export type HandoverDispatchOut = {
  dispatches: { channel: string; status: string; error: string | null }[];
};

export const handoverApi = {
  generate(
    client: ApiClient,
    caseId: string,
    body: {
      hospital_destination: string;
      doctor_registration_number: string;
      doctor_assessment?: string | null;
    },
  ) {
    return client.post<HandoverOut>(`/api/v1/emergency/alerts/${caseId}/handover`, body);
  },
  refreshLink(client: ApiClient, handoverId: string) {
    return client.get<{ url: string; expires_in_seconds: number }>(
      `/api/v1/handover/${handoverId}/link`,
    );
  },
  dispatch(
    client: ApiClient,
    handoverId: string,
    body: { email?: string | null; whatsapp?: string | null },
  ) {
    return client.post<HandoverDispatchOut>(`/api/v1/handover/${handoverId}/dispatch`, body);
  },
};

// --------------------------------------------------------------------- //
// Admin KPIs + monthly export (Slice 10)                                 //
// --------------------------------------------------------------------- //

export type AdminKpis = {
  project_id: string;
  window_label: string;
  window_start: string;
  window_end: string;
  emergency: {
    total_cases: number;
    active_cases: number;
    closed_cases: number;
    average_ack_seconds: number | null;
    average_on_site_seconds: number | null;
  };
  residents: { onboarded: number };
  records: { uploaded: number };
  medicines: {
    consented_residents: number;
    consent_paused_residents: number;
    scheduled_taken: number;
    scheduled_skipped: number;
    missed: number;
    scheduled_slots: number;
    prn_taken: number;
    prn_skipped: number;
    scheduled_adherence_percent: number | null;
  };
};

export const adminApi = {
  kpis(client: ApiClient, days = 30) {
    return client.get<AdminKpis>(`/api/v1/admin/kpis?days=${days}`);
  },
  exportUrl(month: string, format: "csv" | "pdf") {
    return `/api/v1/admin/exports/monthly?month=${month}&format=${format}`;
  },
};
