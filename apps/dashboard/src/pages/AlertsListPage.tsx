/**
 * Slice 15 — live emergency alert feed for clinical staff.
 *
 * Polls `GET /api/v1/emergency/alerts/active` every 5s via TanStack
 * Query. Each card links to the case detail. Status badge color
 * tracks lifecycle severity so an "alerted" case is visually loudest.
 */

import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { emergencyApi, type Alert, type CaseStatus } from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import { Badge, type BadgeProps } from "../components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../components/ui/card";
import { EMPTY_ALERT_FEED } from "../safety";

const POLL_MS = 5_000;

const statusVariant: Record<CaseStatus, BadgeProps["variant"]> = {
  alerted: "destructive",
  acknowledged: "warning",
  en_route: "warning",
  on_site: "warning",
  treated_on_site: "success",
  escalated: "warning",
  closed: "secondary",
};

export function AlertsListPage() {
  const { client } = useAuth();
  const { data, isLoading, error } = useQuery({
    queryKey: ["alerts", "active"],
    queryFn: () => emergencyApi.listActive(client),
    refetchInterval: POLL_MS,
  });

  if (isLoading) {
    return <p className="text-muted-foreground">Loading active alerts…</p>;
  }
  if (error) {
    return (
      <p className="text-destructive" role="alert">
        Could not load the alert feed.
      </p>
    );
  }
  const alerts = data ?? [];
  if (alerts.length === 0) {
    return <p className="text-muted-foreground">{EMPTY_ALERT_FEED}</p>;
  }
  return (
    <div className="grid gap-3">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Live emergency alerts</h1>
        <Badge variant="outline">{alerts.length} active</Badge>
      </header>
      <ul className="grid gap-3">
        {alerts.map((alert) => (
          <li key={alert.id}>
            <AlertCard alert={alert} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function AlertCard({ alert }: { alert: Alert }) {
  const symptoms =
    alert.symptom_codes.length > 0 ? alert.symptom_codes.join(", ") : "none selected";
  return (
    <Link to={`/alerts/${alert.id}`} className="block">
      <Card className="hover:bg-accent/40 transition-colors">
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>
              {alert.resident_name ?? "Resident"} · {alert.flat_villa_number ?? "Flat unknown"}
            </CardTitle>
            <CardDescription>
              {new Date(alert.alert_time).toLocaleString()} · Symptoms: {symptoms}
            </CardDescription>
          </div>
          <Badge variant={statusVariant[alert.status]}>{alert.status}</Badge>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {alert.notification_attempts.map((attempt) => (
            <span
              key={`${alert.id}-${attempt.channel}`}
              className="rounded bg-muted px-2 py-0.5"
            >
              {attempt.channel}: {attempt.status}
            </span>
          ))}
        </CardContent>
      </Card>
    </Link>
  );
}
