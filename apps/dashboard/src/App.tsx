import { useEffect, useMemo, useState } from "react";

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
  symptom_codes: string[];
  location_text: string | null;
  assigned_doctor_id: string | null;
  notification_attempts: Attempt[];
};

const disclaimer =
  "This app does not replace emergency hospital care. In a life-threatening situation, call 108 / 112 immediately.";

const apiBaseDefault = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export default function App() {
  const [apiBase, setApiBase] = useState(apiBaseDefault);
  const [token, setToken] = useState(() => localStorage.getItem("dashboardAccessToken") ?? "");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [status, setStatus] = useState("Idle");
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const activeCount = alerts.length;
  const newest = useMemo(() => alerts[0], [alerts]);

  useEffect(() => {
    localStorage.setItem("dashboardAccessToken", token);
  }, [token]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!token.trim()) {
        setStatus("Enter doctor access token");
        setAlerts([]);
        return;
      }
      setStatus("Refreshing");
      try {
        const resp = await fetch(`${apiBase.replace(/\/$/, "")}/api/v1/emergency/alerts/active`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!resp.ok) {
          setStatus(`Feed unavailable (${resp.status})`);
          return;
        }
        const data = (await resp.json()) as Alert[];
        if (!cancelled) {
          setAlerts(data);
          setStatus("Live");
          setLastUpdated(new Date().toLocaleTimeString());
        }
      } catch {
        if (!cancelled) setStatus("Network error");
      }
    }

    void load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [apiBase, token]);

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
  footer: { maxWidth: 1120, margin: "18px auto 0", color: "#596273", fontSize: 13 },
};
