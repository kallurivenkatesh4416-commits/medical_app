/**
 * Slice 15 — application route tree.
 *
 * /login                      — public
 * /alerts                     — PHI (doctor / nurse / ops)
 * /alerts/:caseId             — PHI, full case detail
 * /residents/:residentId      — PHI, patient profile + history + records + meds
 * /admin/kpis                 — builder_admin only
 * /admin/exports              — builder_admin only
 * /403                        — role mismatch landing
 * /                           — redirects to role's home (/alerts vs /admin/kpis)
 *
 * The redirect on `/` is role-aware so a builder-admin who hits the
 * root URL lands on `/admin/kpis`, not the empty `/alerts` they don't
 * have access to.
 */

import { Navigate, Route, Routes } from "react-router-dom";

import { ADMIN_ROLES, PHI_ROLES, RouteGuard } from "./auth/RouteGuard";
import { useAuth } from "./auth/AuthContext";
import { AppShell } from "./components/AppShell";
import { AdminExportsPage } from "./pages/AdminExportsPage";
import { AdminKpisPage } from "./pages/AdminKpisPage";
import { AlertsListPage } from "./pages/AlertsListPage";
import { CaseDetailPage } from "./pages/CaseDetailPage";
import { ForbiddenPage } from "./pages/ForbiddenPage";
import { LoginPage } from "./pages/LoginPage";
import { PatientProfilePage } from "./pages/PatientProfilePage";

function HomeRedirect() {
  const { status, user } = useAuth();
  if (status === "bootstrapping") return null; // splash handled by RouteGuard
  if (!user) return <Navigate to="/login" replace />;
  if (user.role === "builder_admin") return <Navigate to="/admin/kpis" replace />;
  return <Navigate to="/alerts" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AppShell />}>
        <Route
          path="/alerts"
          element={
            <RouteGuard allow={PHI_ROLES}>
              <AlertsListPage />
            </RouteGuard>
          }
        />
        <Route
          path="/alerts/:caseId"
          element={
            <RouteGuard allow={PHI_ROLES}>
              <CaseDetailPage />
            </RouteGuard>
          }
        />
        <Route
          path="/residents/:residentId"
          element={
            <RouteGuard allow={PHI_ROLES}>
              <PatientProfilePage />
            </RouteGuard>
          }
        />
        <Route
          path="/admin/kpis"
          element={
            <RouteGuard allow={ADMIN_ROLES}>
              <AdminKpisPage />
            </RouteGuard>
          }
        />
        <Route
          path="/admin/exports"
          element={
            <RouteGuard allow={ADMIN_ROLES}>
              <AdminExportsPage />
            </RouteGuard>
          }
        />
        <Route
          path="/403"
          element={
            <RouteGuard allow={[...PHI_ROLES, ...ADMIN_ROLES]}>
              <ForbiddenPage />
            </RouteGuard>
          }
        />
      </Route>
      <Route path="/" element={<HomeRedirect />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
