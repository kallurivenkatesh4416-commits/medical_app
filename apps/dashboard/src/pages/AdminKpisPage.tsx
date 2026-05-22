/**
 * Slice 15 — builder-admin KPI surface.
 *
 * Aggregate counts only. Never renders a resident name, flat number,
 * case id, record id, medicine name, or signed link — same PHI-free
 * contract Slice 10 enforces backend-side. The RouteGuard makes
 * `builder_admin` the only role that can reach this page.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { adminApi } from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Button } from "../components/ui/button";

export function AdminKpisPage() {
  const { client } = useAuth();
  const [days, setDays] = useState("30");
  const window = Math.max(1, Math.min(365, Number.parseInt(days, 10) || 30));
  const kpis = useQuery({
    queryKey: ["admin", "kpis", window],
    queryFn: () => adminApi.kpis(client, window),
  });

  return (
    <div className="grid gap-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Project KPIs (aggregate)</h1>
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void kpis.refetch();
          }}
        >
          <label className="text-sm text-muted-foreground" htmlFor="days">
            Window (days)
          </label>
          <Input
            id="days"
            inputMode="numeric"
            value={days}
            onChange={(e) => setDays(e.target.value)}
            className="w-24"
          />
          <Button type="submit" variant="outline" size="sm" disabled={kpis.isFetching}>
            {kpis.isFetching ? "Loading…" : "Refresh"}
          </Button>
        </form>
      </header>
      {kpis.isLoading ? (
        <p className="text-muted-foreground">Loading KPIs…</p>
      ) : kpis.error || !kpis.data ? (
        <p className="text-destructive" role="alert">
          Could not load KPIs.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-3">
          <KpiCard
            title="Emergency"
            primary={kpis.data.emergency.total_cases}
            caption={`${kpis.data.emergency.closed_cases} closed · ${kpis.data.emergency.active_cases} active`}
          />
          <KpiCard
            title="Response"
            primary={kpis.data.emergency.average_ack_seconds ?? "n/a"}
            caption="avg ack seconds"
          />
          <KpiCard
            title="On-site"
            primary={kpis.data.emergency.average_on_site_seconds ?? "n/a"}
            caption="avg on-site seconds"
          />
          <KpiCard
            title="Residents"
            primary={kpis.data.residents.onboarded}
            caption="onboarded"
          />
          <KpiCard
            title="Records"
            primary={kpis.data.records.uploaded}
            caption="uploaded"
          />
          <KpiCard
            title="Medicine adherence"
            primary={kpis.data.medicines.scheduled_adherence_percent ?? "n/a"}
            caption={`reminders consent: ${kpis.data.medicines.consented_residents}/${kpis.data.medicines.consented_residents + kpis.data.medicines.consent_paused_residents}`}
          />
        </div>
      )}
    </div>
  );
}

function KpiCard({
  title,
  primary,
  caption,
}: {
  title: string;
  primary: number | string;
  caption: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm uppercase tracking-wide text-muted-foreground">
          {title}
        </CardTitle>
        <CardDescription className="text-3xl font-bold text-foreground">
          {primary}
        </CardDescription>
      </CardHeader>
      <CardContent className="text-xs text-muted-foreground">{caption}</CardContent>
    </Card>
  );
}
