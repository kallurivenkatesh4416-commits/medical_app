/**
 * Slice 15 — top-level app frame for every authenticated route.
 *
 * - Header: project / role chip on the left, sign-out on the right.
 * - Body: role-conditional nav links (PHI routes for clinical roles,
 *   admin route for builder_admin).
 * - Footer: the brief §2.1 short disclaimer (every staff surface
 *   carries it, mirroring the mobile pattern).
 */

import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Footer } from "./Footer";

const phiRoles = ["doctor", "nurse", "ops"] as const;

function NavLinkItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `rounded-md px-3 py-2 text-sm font-medium transition-colors ${
          isActive
            ? "bg-primary text-primary-foreground"
            : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
        }`
      }
    >
      {label}
    </NavLink>
  );
}

export function AppShell() {
  const { user, logout } = useAuth();
  const isPhiRole = user
    ? (phiRoles as ReadonlyArray<string>).includes(user.role)
    : false;
  const isAdminRole = user?.role === "builder_admin";

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b">
        <div className="container flex h-16 items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="text-base font-semibold">Emergency Health · Staff</span>
            {user ? (
              <Badge variant="secondary">{user.role}</Badge>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            {user ? (
              <>
                <span className="text-sm text-muted-foreground">
                  {user.full_name ?? user.phone}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    void logout();
                  }}
                >
                  Sign out
                </Button>
              </>
            ) : null}
          </div>
        </div>
        <nav className="container flex h-12 items-center gap-1 overflow-x-auto">
          {isPhiRole ? (
            <>
              <NavLinkItem to="/alerts" label="Alerts" />
            </>
          ) : null}
          {isAdminRole ? (
            <>
              <NavLinkItem to="/admin/kpis" label="KPIs" />
              <NavLinkItem to="/admin/exports" label="Exports" />
            </>
          ) : null}
        </nav>
      </header>
      <main className="container flex-1 py-6">
        <Outlet />
      </main>
      <Footer />
    </div>
  );
}
