import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type Attempt = {
  channel: string;
  recipient_id: string | null;
  status: string;
  provider_ref: string | null;
  error: string | null;
};

type Alert = {
  id: string;
  resident_name: string | null;
  flat_villa_number: string | null;
  status: string;
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
  notification_attempts: Attempt[];
};

type VitalsDraft = {
  blood_pressure_systolic: string;
  blood_pressure_diastolic: string;
  spo2_percent: string;
  heart_rate_bpm: string;
  respiratory_rate_bpm: string;
  temperature_c: string;
};

type AdherenceTotals = {
  taken: number;
  skipped: number;
  missed: number;
  scheduled_slots: number;
};

type AdherenceSchedule = {
  schedule_id: string;
  name: string;
  frequency: string;
  active: boolean;
  taken: number;
  skipped: number;
  missed: number;
  scheduled_slots: number;
};

type Adherence = {
  resident_id: string;
  window_days: number;
  totals: AdherenceTotals;
  schedules: AdherenceSchedule[];
};

type AdminKpis = {
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

const emptyVitals: VitalsDraft = {
  blood_pressure_systolic: "",
  blood_pressure_diastolic: "",
  spo2_percent: "",
  heart_rate_bpm: "",
  respiratory_rate_bpm: "",
  temperature_c: "",
};

const disclaimer =
  "This app does not replace emergency hospital care. In a life-threatening situation, call 108 / 112 immediately.";

const apiBaseDefault = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const currentMonth = () => new Date().toISOString().slice(0, 7);

export default function App() {
  const [apiBase, setApiBase] = useState(apiBaseDefault);
  const [token, setToken] = useState(() => localStorage.getItem("dashboardAccessToken") ?? "");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [status, setStatus] = useState("Idle");
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("");
  const [vitals, setVitals] = useState<VitalsDraft>(emptyVitals);
  const [noteBody, setNoteBody] = useState("");
  const [doctorName, setDoctorName] = useState("");
  const [doctorRegistration, setDoctorRegistration] = useState("");
  const [adviceGiven, setAdviceGiven] = useState("");
  const [patientConsent, setPatientConsent] = useState(false);
  // Slice 8 — handover PDF state. The signed link is kept in memory only;
  // we never persist a capability token to localStorage (15-min window).
  const [hospitalDestination, setHospitalDestination] = useState("");
  const [doctorAssessment, setDoctorAssessment] = useState("");
  const [handoverLink, setHandoverLink] = useState<string | null>(null);
  const [handoverId, setHandoverId] = useState<string | null>(null);
  const [dispatchEmail, setDispatchEmail] = useState("");
  const [dispatchWhatsapp, setDispatchWhatsapp] = useState("");
  const [handoverStatus, setHandoverStatus] = useState("");
  // Slice 9 — per-resident adherence view. The doctor pastes a resident id
  // (no resident search yet — that lands with the admin dashboard slice).
  const [adherenceResidentId, setAdherenceResidentId] = useState("");
  const [adherenceDays, setAdherenceDays] = useState("7");
  const [adherenceResult, setAdherenceResult] = useState<Adherence | null>(null);
  const [adherenceStatus, setAdherenceStatus] = useState("");
  // Slice 10 — builder-admin aggregate KPIs. This panel never renders patient
  // names, case ids, record ids, medicine names, or signed links.
  const [adminDays, setAdminDays] = useState("30");
  const [adminMonth, setAdminMonth] = useState(currentMonth);
  const [adminKpis, setAdminKpis] = useState<AdminKpis | null>(null);
  const [adminStatus, setAdminStatus] = useState("");

  const activeCount = alerts.length;
  const newest = useMemo(() => alerts[0], [alerts]);
  const selected = useMemo(
    () => alerts.find((alert) => alert.id === selectedId) ?? newest,
    [alerts, newest, selectedId],
  );
  const apiBaseClean = apiBase.replace(/\/$/, "");

  useEffect(() => {
    localStorage.setItem("dashboardAccessToken", token);
  }, [token]);

  const loadAlerts = useCallback(async () => {
    if (!token.trim()) {
      setStatus("Enter doctor access token");
      setAlerts([]);
      return;
    }
    setStatus("Refreshing");
    try {
      const resp = await fetch(`${apiBaseClean}/api/v1/emergency/alerts/active`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) {
        setStatus(`Feed unavailable (${resp.status})`);
        return;
      }
      const data = (await resp.json()) as Alert[];
      setAlerts(data);
      setStatus("Live");
      setLastUpdated(new Date().toLocaleTimeString());
      setSelectedId((current) => (current && data.some((a) => a.id === current) ? current : data[0]?.id ?? null));
    } catch {
      setStatus("Network error");
    }
  }, [apiBaseClean, token]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!cancelled) await loadAlerts();
    }

    void load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [loadAlerts]);

  async function postJson(path: string, body: unknown) {
    if (!token.trim()) return;
    setActionStatus("Saving");
    const resp = await fetch(`${apiBaseClean}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      setActionStatus(`Action failed (${resp.status})`);
      return;
    }
    setActionStatus("Saved");
    await loadAlerts();
  }

  function transition(targetStatus: string, resolvedOutcome?: string) {
    if (!selected) return;
    void postJson(`/api/v1/emergency/alerts/${selected.id}/transition`, {
      target_status: targetStatus,
      resolved_outcome: resolvedOutcome ?? null,
    });
  }

  function submitVitals(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const body = Object.fromEntries(
      Object.entries(vitals)
        .filter(([, value]) => value.trim() !== "")
        .map(([key, value]) => [key, key === "temperature_c" ? Number.parseFloat(value) : Number.parseInt(value, 10)]),
    );
    void postJson(`/api/v1/emergency/alerts/${selected.id}/vitals`, body);
    setVitals(emptyVitals);
  }

  function submitNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    void postJson(`/api/v1/emergency/alerts/${selected.id}/notes`, {
      note_type: "treatment",
      body: noteBody,
      doctor_name: doctorName,
      doctor_registration_number: doctorRegistration,
      consultation_timestamp: new Date().toISOString(),
      advice_given: adviceGiven,
      patient_consent_obtained: patientConsent,
    });
    setNoteBody("");
    setAdviceGiven("");
  }

  async function generateHandover(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !token.trim()) return;
    setHandoverStatus("Generating");
    const resp = await fetch(
      `${apiBaseClean}/api/v1/emergency/alerts/${selected.id}/handover`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          hospital_destination: hospitalDestination,
          // The dashboard reuses the registration number captured for the
          // treatment note — same doctor, same handover signature line.
          doctor_registration_number: doctorRegistration,
          doctor_assessment: doctorAssessment || null,
        }),
      },
    );
    if (!resp.ok) {
      setHandoverStatus(`Handover failed (${resp.status})`);
      return;
    }
    const data = (await resp.json()) as { id: string; signed_url: string };
    setHandoverId(data.id);
    setHandoverLink(data.signed_url);
    setHandoverStatus("Ready (link expires in 15 min)");
  }

  async function dispatchHandover(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!handoverId || !token.trim()) return;
    setHandoverStatus("Dispatching");
    const resp = await fetch(
      `${apiBaseClean}/api/v1/handover/${handoverId}/dispatch`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email: dispatchEmail || null,
          whatsapp: dispatchWhatsapp || null,
        }),
      },
    );
    if (!resp.ok) {
      setHandoverStatus(`Dispatch failed (${resp.status})`);
      return;
    }
    const data = (await resp.json()) as {
      dispatches: { channel: string; status: string; error: string | null }[];
    };
    const summary = data.dispatches
      .map((d) => `${d.channel}: ${d.status}${d.error ? ` (${d.error})` : ""}`)
      .join(" · ");
    setHandoverStatus(summary || "Dispatched");
  }

  async function loadAdherence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token.trim() || !adherenceResidentId.trim()) return;
    setAdherenceStatus("Loading");
    setAdherenceResult(null);
    const days = Number.parseInt(adherenceDays, 10) || 7;
    const resp = await fetch(
      `${apiBaseClean}/api/v1/residents/${adherenceResidentId.trim()}/medicines/adherence?days=${days}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!resp.ok) {
      setAdherenceStatus(`Adherence read failed (${resp.status})`);
      return;
    }
    const data = (await resp.json()) as Adherence;
    setAdherenceResult(data);
    setAdherenceStatus(`Loaded (${data.window_days}-day window)`);
  }

  async function loadAdminKpis(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token.trim()) return;
    setAdminStatus("Loading");
    const days = Number.parseInt(adminDays, 10) || 30;
    const resp = await fetch(`${apiBaseClean}/api/v1/admin/kpis?days=${days}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) {
      setAdminStatus(`KPI read failed (${resp.status})`);
      setAdminKpis(null);
      return;
    }
    const data = (await resp.json()) as AdminKpis;
    setAdminKpis(data);
    setAdminStatus(`Loaded ${data.window_label}`);
  }

  async function downloadAdminExport(format: "csv" | "pdf") {
    if (!token.trim() || !adminMonth.trim()) return;
    setAdminStatus(`Preparing ${format.toUpperCase()}`);
    const resp = await fetch(
      `${apiBaseClean}/api/v1/admin/exports/monthly?month=${adminMonth.trim()}&format=${format}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!resp.ok) {
      setAdminStatus(`Export failed (${resp.status})`);
      return;
    }
    const blob = await resp.blob();
    const href = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = href;
    link.download = `admin-kpis-${adminMonth}.${format}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(href);
    setAdminStatus(`${format.toUpperCase()} ready`);
  }

  async function refreshHandoverLink() {
    if (!handoverId || !token.trim()) return;
    setHandoverStatus("Refreshing link");
    const resp = await fetch(
      `${apiBaseClean}/api/v1/handover/${handoverId}/link`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!resp.ok) {
      setHandoverStatus(`Link refresh failed (${resp.status})`);
      return;
    }
    const data = (await resp.json()) as { url: string };
    setHandoverLink(data.url);
    setHandoverStatus("Ready (link expires in 15 min)");
  }

  return (
    <main style={styles.shell}>
      <section style={styles.header}>
        <div>
          <p style={styles.kicker}>Doctor Dashboard</p>
          <h1 style={styles.title}>Live Emergency Alerts</h1>
        </div>
        <div style={activeCount > 0 ? styles.countHot : styles.countQuiet}>{activeCount}</div>
      </section>

      {newest ? (
        <section style={styles.banner}>
          <div>
            <p style={styles.bannerLabel}>Newest alert</p>
            <h2 style={styles.bannerTitle}>
              {newest.resident_name ?? "Resident"} · {newest.flat_villa_number ?? "Flat unknown"}
            </h2>
            <p style={styles.bannerMeta}>
              {new Date(newest.alert_time).toLocaleString()} · {newest.status}
            </p>
          </div>
        </section>
      ) : null}

      <section style={styles.controls}>
        <label style={styles.field}>
          API base
          <input value={apiBase} onChange={(event) => setApiBase(event.target.value)} style={styles.input} />
        </label>
        <label style={styles.field}>
          Access token
          <input
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="Bearer token from /api/v1/auth/otp/verify"
            style={styles.input}
          />
        </label>
        <div style={styles.status}>
          <span>{status}</span>
          <span>{lastUpdated ? `Updated ${lastUpdated}` : ""}</span>
        </div>
      </section>

      <section style={styles.workspace}>
        <div style={styles.workspaceHeader}>
          <div>
            <p style={styles.kicker}>Admin KPIs</p>
            <h2 style={styles.panelTitle}>Project Aggregate View</h2>
          </div>
          <span style={styles.status}>{adminStatus}</span>
        </div>
        <form onSubmit={loadAdminKpis} style={styles.formGrid}>
          <label style={styles.field}>
            Window (days)
            <input
              value={adminDays}
              onChange={(event) => setAdminDays(event.target.value)}
              style={styles.input}
              inputMode="numeric"
            />
          </label>
          <label style={styles.field}>
            Export month
            <input
              value={adminMonth}
              onChange={(event) => setAdminMonth(event.target.value)}
              style={styles.input}
              placeholder="YYYY-MM"
            />
          </label>
          <button type="submit" style={styles.actionButton}>
            Load KPIs
          </button>
          <div style={styles.exportActions}>
            <button type="button" onClick={() => void downloadAdminExport("csv")} style={styles.smallButton}>
              CSV
            </button>
            <button type="button" onClick={() => void downloadAdminExport("pdf")} style={styles.smallButton}>
              PDF
            </button>
          </div>
        </form>
        {adminKpis ? (
          <div style={styles.metricGrid}>
            <div style={styles.metricTile}>
              <span style={styles.kicker}>Emergency</span>
              <strong style={styles.metricValue}>{adminKpis.emergency.total_cases}</strong>
              <span style={styles.muted}>
                {adminKpis.emergency.closed_cases} closed · {adminKpis.emergency.active_cases} active
              </span>
            </div>
            <div style={styles.metricTile}>
              <span style={styles.kicker}>Response</span>
              <strong style={styles.metricValue}>
                {adminKpis.emergency.average_ack_seconds ?? "n/a"}
              </strong>
              <span style={styles.muted}>avg ack seconds</span>
            </div>
            <div style={styles.metricTile}>
              <span style={styles.kicker}>Residents</span>
              <strong style={styles.metricValue}>{adminKpis.residents.onboarded}</strong>
              <span style={styles.muted}>onboarded</span>
            </div>
            <div style={styles.metricTile}>
              <span style={styles.kicker}>Records</span>
              <strong style={styles.metricValue}>{adminKpis.records.uploaded}</strong>
              <span style={styles.muted}>uploaded</span>
            </div>
            <div style={styles.metricTile}>
              <span style={styles.kicker}>Medicines</span>
              <strong style={styles.metricValue}>
                {adminKpis.medicines.scheduled_adherence_percent ?? "n/a"}
              </strong>
              <span style={styles.muted}>scheduled adherence %</span>
            </div>
            <div style={styles.metricTile}>
              <span style={styles.kicker}>Reminder Consent</span>
              <strong style={styles.metricValue}>{adminKpis.medicines.consented_residents}</strong>
              <span style={styles.muted}>
                {adminKpis.medicines.consent_paused_residents} paused
              </span>
            </div>
          </div>
        ) : null}
      </section>

      <section style={styles.feed}>
        {alerts.length === 0 ? (
          <div style={styles.empty}>No active emergency alerts.</div>
        ) : (
          alerts.map((alert) => (
            <article key={alert.id} style={styles.row}>
              <div>
                <div style={styles.rowTop}>
                  <strong>{alert.resident_name ?? "Resident"}</strong>
                  <span style={styles.badge}>{alert.status}</span>
                </div>
                <div style={styles.muted}>
                  {alert.flat_villa_number ?? "Flat unknown"} ·{" "}
                  {alert.location_text ?? "No location note"}
                </div>
                <div style={styles.muted}>
                  Symptoms: {alert.symptom_codes.length ? alert.symptom_codes.join(", ") : "none selected"}
                </div>
              </div>
              <div style={styles.attempts}>
                <button type="button" onClick={() => setSelectedId(alert.id)} style={styles.smallButton}>
                  Select
                </button>
                {alert.notification_attempts.map((attempt) => (
                  <span key={`${alert.id}-${attempt.channel}`} style={styles.attempt}>
                    {attempt.channel}: {attempt.status}
                  </span>
                ))}
              </div>
            </article>
          ))
        )}
      </section>

      {selected ? (
        <section style={styles.workspace}>
          <div style={styles.workspaceHeader}>
            <div>
              <p style={styles.kicker}>Selected Case</p>
              <h2 style={styles.panelTitle}>
                {selected.resident_name ?? "Resident"} · {selected.status}
              </h2>
            </div>
            <span style={styles.status}>{actionStatus}</span>
          </div>

          <div style={styles.actions}>
            {selected.status === "alerted" ? (
              <button onClick={() => transition("acknowledged")} style={styles.actionButton}>
                Acknowledge
              </button>
            ) : null}
            {selected.status === "acknowledged" ? (
              <button onClick={() => transition("en_route")} style={styles.actionButton}>
                En Route
              </button>
            ) : null}
            {selected.status === "en_route" ? (
              <button onClick={() => transition("on_site")} style={styles.actionButton}>
                On Site
              </button>
            ) : null}
            {selected.status === "on_site" ? (
              <>
                <button
                  onClick={() => transition("treated_on_site", "Treated on site")}
                  style={styles.actionButton}
                >
                  Treated On Site
                </button>
                <button
                  onClick={() => transition("escalated", "Escalated to hospital")}
                  style={styles.actionButton}
                >
                  Escalate
                </button>
              </>
            ) : null}
            {selected.status === "treated_on_site" || selected.status === "escalated" ? (
              <button onClick={() => transition("closed")} style={styles.actionButton}>
                Close
              </button>
            ) : null}
          </div>

          <form onSubmit={submitVitals} style={styles.formGrid}>
            {Object.keys(emptyVitals).map((key) => (
              <label key={key} style={styles.field}>
                {key.replaceAll("_", " ")}
                <input
                  value={vitals[key as keyof VitalsDraft]}
                  onChange={(event) => setVitals({ ...vitals, [key]: event.target.value })}
                  style={styles.input}
                  inputMode="decimal"
                />
              </label>
            ))}
            <button type="submit" style={styles.actionButton}>
              Save Vitals
            </button>
          </form>

          <form onSubmit={submitNote} style={styles.noteForm}>
            <label style={styles.field}>
              Note
              <textarea value={noteBody} onChange={(event) => setNoteBody(event.target.value)} style={styles.textarea} />
            </label>
            <label style={styles.field}>
              Doctor name
              <input value={doctorName} onChange={(event) => setDoctorName(event.target.value)} style={styles.input} />
            </label>
            <label style={styles.field}>
              Registration number
              <input
                value={doctorRegistration}
                onChange={(event) => setDoctorRegistration(event.target.value)}
                style={styles.input}
              />
            </label>
            <label style={styles.field}>
              Advice
              <textarea
                value={adviceGiven}
                onChange={(event) => setAdviceGiven(event.target.value)}
                style={styles.textarea}
              />
            </label>
            <label style={styles.checkboxRow}>
              <input
                type="checkbox"
                checked={patientConsent}
                onChange={(event) => setPatientConsent(event.target.checked)}
              />
              Patient consent recorded
            </label>
            <button type="submit" style={styles.actionButton}>
              Save Note
            </button>
          </form>

          <div style={styles.handoverPanel}>
            <p style={styles.kicker}>Hospital Handover PDF</p>
            <form onSubmit={generateHandover} style={styles.noteForm}>
              <label style={styles.field}>
                Hospital destination
                <input
                  value={hospitalDestination}
                  onChange={(event) => setHospitalDestination(event.target.value)}
                  style={styles.input}
                  placeholder="e.g. Apollo Hospital, Sarjapur Road"
                />
              </label>
              <label style={styles.field}>
                Doctor's refined assessment (optional)
                <textarea
                  value={doctorAssessment}
                  onChange={(event) => setDoctorAssessment(event.target.value)}
                  style={styles.textarea}
                />
              </label>
              <button type="submit" style={styles.actionButton}>
                Generate Handover PDF
              </button>
            </form>
            {handoverLink ? (
              <div style={styles.handoverLinkRow}>
                <a href={handoverLink} target="_blank" rel="noreferrer" style={styles.handoverLink}>
                  Open signed PDF
                </a>
                <button type="button" onClick={refreshHandoverLink} style={styles.smallButton}>
                  Refresh link
                </button>
              </div>
            ) : null}
            {handoverId ? (
              <form onSubmit={dispatchHandover} style={styles.noteForm}>
                <label style={styles.field}>
                  Hospital email
                  <input
                    type="email"
                    value={dispatchEmail}
                    onChange={(event) => setDispatchEmail(event.target.value)}
                    style={styles.input}
                    placeholder="er@hospital.example"
                  />
                </label>
                <label style={styles.field}>
                  Hospital WhatsApp
                  <input
                    value={dispatchWhatsapp}
                    onChange={(event) => setDispatchWhatsapp(event.target.value)}
                    style={styles.input}
                    placeholder="+919xxxxxxxxx"
                  />
                </label>
                <button type="submit" style={styles.actionButton}>
                  Send Handover
                </button>
              </form>
            ) : null}
            {handoverStatus ? <p style={styles.muted}>{handoverStatus}</p> : null}
          </div>
        </section>
      ) : null}

      <section style={styles.workspace}>
        <p style={styles.kicker}>Medicine Adherence</p>
        <form onSubmit={loadAdherence} style={styles.formGrid}>
          <label style={styles.field}>
            Resident ID
            <input
              value={adherenceResidentId}
              onChange={(event) => setAdherenceResidentId(event.target.value)}
              style={styles.input}
              placeholder="paste resident UUID"
            />
          </label>
          <label style={styles.field}>
            Window (days)
            <input
              value={adherenceDays}
              onChange={(event) => setAdherenceDays(event.target.value)}
              style={styles.input}
              inputMode="numeric"
            />
          </label>
          <button type="submit" style={styles.actionButton}>
            Load Adherence
          </button>
        </form>
        {adherenceStatus ? <p style={styles.muted}>{adherenceStatus}</p> : null}
        {adherenceResult ? (
          <div>
            <p style={styles.muted}>
              Totals — taken {adherenceResult.totals.taken} · skipped{" "}
              {adherenceResult.totals.skipped} · missed{" "}
              {adherenceResult.totals.missed} · scheduled{" "}
              {adherenceResult.totals.scheduled_slots}
            </p>
            <table style={styles.adherenceTable}>
              <thead>
                <tr>
                  <th style={styles.adherenceCell}>Medicine</th>
                  <th style={styles.adherenceCell}>Frequency</th>
                  <th style={styles.adherenceCell}>Active</th>
                  <th style={styles.adherenceCell}>Taken</th>
                  <th style={styles.adherenceCell}>Skipped</th>
                  <th style={styles.adherenceCell}>Missed</th>
                </tr>
              </thead>
              <tbody>
                {adherenceResult.schedules.map((row) => (
                  <tr key={row.schedule_id}>
                    <td style={styles.adherenceCell}>{row.name}</td>
                    <td style={styles.adherenceCell}>{row.frequency.replaceAll("_", " ")}</td>
                    <td style={styles.adherenceCell}>{row.active ? "yes" : "no"}</td>
                    <td style={styles.adherenceCell}>{row.taken}</td>
                    <td style={styles.adherenceCell}>{row.skipped}</td>
                    <td style={styles.adherenceCell}>{row.missed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {/* Slice 11 polish: empty state on the adherence table so a
                doctor reading a resident with no schedules sees a quiet
                helpful sentence rather than an empty grid. */}
            {adherenceResult.schedules.length === 0 ? (
              <p style={styles.muted}>
                No medicine schedules on file for this resident yet.
              </p>
            ) : null}
          </div>
        ) : null}
      </section>

      <footer style={styles.footer}>{disclaimer}</footer>
    </main>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    minHeight: "100vh",
    margin: 0,
    padding: 24,
    boxSizing: "border-box",
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
    background: "#f6f7f9",
    color: "#1d2430",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 16,
    maxWidth: 1120,
    margin: "0 auto 16px",
  },
  kicker: { margin: "0 0 4px", fontSize: 13, color: "#596273", textTransform: "uppercase" },
  title: { margin: 0, fontSize: 30, fontWeight: 700 },
  countHot: {
    minWidth: 58,
    height: 58,
    borderRadius: 8,
    display: "grid",
    placeItems: "center",
    background: "#bd1e2d",
    color: "white",
    fontSize: 28,
    fontWeight: 800,
  },
  countQuiet: {
    minWidth: 58,
    height: 58,
    borderRadius: 8,
    display: "grid",
    placeItems: "center",
    background: "#d7dde6",
    color: "#394252",
    fontSize: 28,
    fontWeight: 800,
  },
  banner: {
    maxWidth: 1120,
    margin: "0 auto 16px",
    padding: 18,
    borderRadius: 8,
    borderLeft: "6px solid #bd1e2d",
    background: "#fff5f5",
  },
  bannerLabel: { margin: "0 0 6px", color: "#8c1d2b", fontWeight: 700 },
  bannerTitle: { margin: 0, fontSize: 22 },
  bannerMeta: { margin: "8px 0 0", color: "#596273" },
  controls: {
    maxWidth: 1120,
    margin: "0 auto 16px",
    display: "grid",
    gridTemplateColumns: "minmax(200px, 320px) 1fr minmax(160px, 220px)",
    gap: 12,
    alignItems: "end",
  },
  field: { display: "grid", gap: 6, fontSize: 13, color: "#596273" },
  input: {
    minHeight: 38,
    borderRadius: 6,
    border: "1px solid #c9d0da",
    padding: "0 10px",
    fontSize: 14,
  },
  status: {
    minHeight: 38,
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    gap: 2,
    color: "#596273",
    fontSize: 13,
  },
  feed: { maxWidth: 1120, margin: "0 auto", display: "grid", gap: 10 },
  empty: {
    minHeight: 160,
    display: "grid",
    placeItems: "center",
    border: "1px dashed #c9d0da",
    borderRadius: 8,
    color: "#596273",
    background: "white",
  },
  row: {
    display: "grid",
    gridTemplateColumns: "1fr auto",
    gap: 16,
    padding: 16,
    borderRadius: 8,
    background: "white",
    border: "1px solid #e1e5eb",
  },
  rowTop: { display: "flex", gap: 10, alignItems: "center", marginBottom: 6 },
  badge: {
    borderRadius: 999,
    background: "#fff0cc",
    color: "#6b4b00",
    padding: "3px 8px",
    fontSize: 12,
    fontWeight: 700,
  },
  muted: { color: "#596273", fontSize: 14, marginTop: 4 },
  attempts: { display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", justifyContent: "end" },
  attempt: { background: "#eef2f7", borderRadius: 6, padding: "5px 8px", fontSize: 13 },
  smallButton: {
    minHeight: 32,
    borderRadius: 6,
    border: "1px solid #c9d0da",
    background: "white",
    color: "#1d2430",
  },
  workspace: {
    maxWidth: 1120,
    margin: "16px auto 0",
    padding: 16,
    borderRadius: 8,
    background: "white",
    border: "1px solid #e1e5eb",
    display: "grid",
    gap: 14,
  },
  workspaceHeader: { display: "flex", justifyContent: "space-between", gap: 16, alignItems: "start" },
  panelTitle: { margin: 0, fontSize: 20 },
  actions: { display: "flex", flexWrap: "wrap", gap: 8 },
  actionButton: {
    minHeight: 40,
    borderRadius: 6,
    border: 0,
    background: "#1f6f5b",
    color: "white",
    padding: "0 14px",
    fontWeight: 700,
  },
  formGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
    gap: 10,
    alignItems: "end",
  },
  exportActions: { display: "flex", gap: 8, alignItems: "end", minHeight: 40 },
  metricGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
    gap: 10,
  },
  metricTile: {
    minHeight: 112,
    border: "1px solid #d9dee7",
    borderRadius: 8,
    padding: 12,
    display: "grid",
    alignContent: "space-between",
    background: "#fbfcfe",
  },
  metricValue: { display: "block", fontSize: 26, lineHeight: 1.1, color: "#1d2430" },
  noteForm: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 },
  textarea: {
    minHeight: 82,
    borderRadius: 6,
    border: "1px solid #c9d0da",
    padding: 10,
    fontSize: 14,
    resize: "vertical",
  },
  checkboxRow: { display: "flex", alignItems: "center", gap: 8, color: "#596273", fontSize: 14 },
  handoverPanel: {
    marginTop: 6,
    paddingTop: 12,
    borderTop: "1px dashed #c9d0da",
    display: "grid",
    gap: 10,
  },
  handoverLinkRow: { display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" },
  handoverLink: {
    color: "#1f6f5b",
    fontWeight: 700,
    textDecoration: "underline",
  },
  adherenceTable: {
    width: "100%",
    borderCollapse: "collapse",
    marginTop: 6,
  },
  adherenceCell: {
    textAlign: "left",
    padding: "6px 8px",
    borderBottom: "1px solid #d6dae2",
    fontSize: 13,
  },
  footer: { maxWidth: 1120, margin: "18px auto 0", color: "#596273", fontSize: 13 },
};
