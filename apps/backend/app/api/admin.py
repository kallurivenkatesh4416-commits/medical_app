"""Aggregate admin dashboard API (PLAN.md Slice 10).

Builder admins get project-level KPIs and monthly PDF/CSV exports. The
surface is non-PHI by construction: no patient drill-in, no names, no case
ids, no record ids, and no signed links.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlmodel import Session

from app.enums import Role
from app.models.user import User
from app.security.deps import client_ip, get_db, require_roles
from app.services import admin_service

router = APIRouter(prefix="/api/v1", tags=["admin"])

admin_kpi_roles = require_roles(Role.DOCTOR, Role.NURSE, Role.OPS, Role.BUILDER_ADMIN)


class EmergencyAdminKpis(BaseModel):
    total_cases: int
    active_cases: int
    closed_cases: int
    average_ack_seconds: float | None
    average_on_site_seconds: float | None


class ResidentAdminKpis(BaseModel):
    onboarded: int


class RecordAdminKpis(BaseModel):
    uploaded: int


class MedicineAdminKpis(BaseModel):
    consented_residents: int
    consent_paused_residents: int
    scheduled_taken: int
    scheduled_skipped: int
    missed: int
    scheduled_slots: int
    prn_taken: int
    prn_skipped: int
    scheduled_adherence_percent: float | None


class AdminKpisOut(BaseModel):
    project_id: uuid.UUID
    window_label: str
    window_start: str
    window_end: str
    emergency: EmergencyAdminKpis
    residents: ResidentAdminKpis
    records: RecordAdminKpis
    medicines: MedicineAdminKpis


@router.get("/admin/kpis", response_model=AdminKpisOut)
def admin_kpis(
    request: Request,
    days: int = Query(default=30, ge=1, le=366),
    actor: User = Depends(admin_kpi_roles),
    session: Session = Depends(get_db),
) -> dict:
    return admin_service.admin_kpis(
        session,
        actor=actor,
        window=admin_service.rolling_window(days),
        from_ip=client_ip(request),
    )


@router.get("/admin/exports/monthly")
def monthly_export(
    request: Request,
    month: str = Query(pattern=r"^\d{4}-\d{2}$"),
    format: Literal["csv", "pdf"] = "csv",
    actor: User = Depends(admin_kpi_roles),
    session: Session = Depends(get_db),
) -> Response:
    summary = admin_service.monthly_export_context(
        session,
        actor=actor,
        month=month,
        export_format=format,
        from_ip=client_ip(request),
    )
    if format == "pdf":
        data = admin_service.render_pdf(summary)
        filename = admin_service.export_filename(summary, "pdf")
        media_type = "application/pdf"
    else:
        data = admin_service.render_csv(summary)
        filename = admin_service.export_filename(summary, "csv")
        media_type = "text/csv; charset=utf-8"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
