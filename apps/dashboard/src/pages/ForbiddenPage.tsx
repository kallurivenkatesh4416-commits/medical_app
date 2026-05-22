/**
 * Slice 15 — 403 page for role-blocked routes.
 *
 * Rendered when an authenticated user hits a route their role does
 * not allow (e.g. a builder_admin clicks an `/alerts/*` link from
 * the URL bar — the on-page nav already hides those, but URL pasting
 * is a real flow). NOT a logout — the session is still valid, the
 * user just lacks permission for this surface. Matches the backend
 * RBAC semantics: a `builder_admin` bearer can hit `/admin/*` but
 * gets 403 from `forbid_phi_roles` on patient-level routes.
 */

import { Link } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { Button } from "../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../components/ui/card";

export function ForbiddenPage() {
  const { user } = useAuth();
  const home = user?.role === "builder_admin" ? "/admin/kpis" : "/alerts";
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>You can't view that surface</CardTitle>
          <CardDescription>
            Your role ({user?.role ?? "unknown"}) is not permitted on this
            route. This is enforced both here and by the backend's
            role-based access controls.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild>
            <Link to={home}>Back to your dashboard</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
