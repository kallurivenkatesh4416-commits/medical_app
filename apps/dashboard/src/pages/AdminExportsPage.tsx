/**
 * Slice 15 — builder-admin monthly export.
 *
 * Streams CSV / PDF aggregates from `GET /admin/exports/monthly`.
 * Same PHI-free contract as the KPI surface — backend audits the
 * download as `ADMIN_EXPORT_GENERATED` and refuses to embed any
 * patient identifiers.
 *
 * The binary download rides `ApiClient.requestRaw` so the bearer
 * token + auto-refresh + sessionExpired handling all stay
 * consistent with the JSON pages.
 */

import { useState } from "react";

import { adminApi } from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import { Button } from "../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../components/ui/card";
import { Input } from "../components/ui/input";

function currentMonth(): string {
  return new Date().toISOString().slice(0, 7);
}

export function AdminExportsPage() {
  const { client } = useAuth();
  const [month, setMonth] = useState(currentMonth());
  const [busy, setBusy] = useState<"csv" | "pdf" | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  async function download(format: "csv" | "pdf") {
    if (!month.trim()) return;
    setBusy(format);
    setStatus(null);
    try {
      const resp = await client.requestRaw(
        adminApi.exportUrl(month.trim(), format),
        {
          method: "GET",
          headers: {
            Accept: format === "csv" ? "text/csv" : "application/pdf",
          },
        },
      );
      const blob = await resp.blob();
      const href = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = `admin-kpis-${month}.${format}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(href);
      setStatus(`${format.toUpperCase()} downloaded.`);
    } catch (e) {
      setStatus(
        e instanceof Error
          ? `Export failed: ${e.message}`
          : "Export failed.",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="grid gap-6">
      <h1 className="text-xl font-semibold">Monthly exports</h1>
      <Card>
        <CardHeader>
          <CardTitle>Aggregate operational metrics</CardTitle>
          <CardDescription>
            Exports never include patient names, flat numbers, case ids,
            record ids, medicine names, or signed links — same contract
            as the on-screen KPI view.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-[1fr_auto_auto]">
          <label className="space-y-1 text-sm">
            <span className="font-medium">Month (YYYY-MM)</span>
            <Input
              value={month}
              onChange={(e) => setMonth(e.target.value)}
              placeholder="YYYY-MM"
            />
          </label>
          <Button
            variant="outline"
            onClick={() => void download("csv")}
            disabled={busy !== null || !month.trim()}
            className="self-end"
          >
            {busy === "csv" ? "Preparing…" : "Download CSV"}
          </Button>
          <Button
            onClick={() => void download("pdf")}
            disabled={busy !== null || !month.trim()}
            className="self-end"
          >
            {busy === "pdf" ? "Preparing…" : "Download PDF"}
          </Button>
        </CardContent>
        {status ? (
          <CardContent className="text-sm text-muted-foreground" role="status">
            {status}
          </CardContent>
        ) : null}
      </Card>
    </div>
  );
}
