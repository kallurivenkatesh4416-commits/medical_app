/**
 * Slice 15 — route-level role gate.
 *
 * Wraps a child route with a role check. Three outcomes:
 *   - bootstrap pending → render <SplashLoader/> so we never paint
 *     "logged out" while the silent refresh is still in flight.
 *   - unauthenticated → redirect to /login with `?next=` so the
 *     post-login redirect lands the user back where they tried to go.
 *   - role mismatch → redirect to /403 (the on-screen "forbidden"
 *     view), NOT logout. The user has a valid session; they just
 *     lack permission for this route. This mirrors Slice 6's RBAC
 *     pattern (a doctor's bearer is valid, they just cannot touch
 *     admin-only endpoints — backend still enforces the boundary).
 *
 * The brief's RBAC promise (`builder_admin` never sees PHI) is
 * preserved by routing: `/alerts/*` and `/residents/*` require a
 * PHI role; `/admin/*` requires `builder_admin`. The backend's
 * `forbid_phi_roles` dependency is the actual security boundary —
 * this guard is the UX layer that keeps a user out of a route they'd
 * just hit a 403 on anyway.
 */

import { Navigate, useLocation } from "react-router-dom";

import { ADMIN_ROLES, PHI_ROLES, type Role } from "../api/auth";
import { useAuth } from "./AuthContext";

export function SplashLoader() {
  return (
    <div className="min-h-screen flex items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}

type RouteGuardProps = {
  children: React.ReactNode;
  allow: ReadonlyArray<Role>;
};

export function RouteGuard({ children, allow }: RouteGuardProps) {
  const { status, user } = useAuth();
  const location = useLocation();

  if (status === "bootstrapping") return <SplashLoader />;
  if (status === "anonymous" || !user) {
    const next = encodeURIComponent(`${location.pathname}${location.search}`);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  if (!allow.includes(user.role)) {
    return <Navigate to="/403" replace />;
  }
  return <>{children}</>;
}

export { PHI_ROLES, ADMIN_ROLES };
