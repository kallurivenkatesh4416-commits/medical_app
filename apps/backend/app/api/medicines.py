"""Medicine reminders API (PLAN.md Slice 9 / brief §6).

Endpoints:

Resident-self:
  POST   /api/v1/me/medicines/schedules         create own schedule (consent-gated)
  GET    /api/v1/me/medicines/schedules         list own schedules
  POST   /api/v1/me/medicines/schedules/{id}/deactivate
  POST   /api/v1/me/medicines/doses             log own dose (taken | skipped)

Staff (doctor on behalf / list / adherence):
  POST   /api/v1/residents/{resident_id}/medicines/schedules
                                                doctor creates a schedule
  GET    /api/v1/residents/{resident_id}/medicines/schedules
                                                doctor/nurse/ops list
  POST   /api/v1/residents/{resident_id}/medicines/schedules/{id}/deactivate
                                                doctor stops a schedule
  GET    /api/v1/residents/{resident_id}/medicines/adherence?days=N
                                                aggregate adherence; PHI;
                                                EMERGENCY_SHARE_WITH_DOCTOR gated

PHI surfaces are PHI-role-blocked (builder_admin / security_desk forbidden).
Tenant isolation, consent gates, and audit follow the Slice 3/4/7/8 patterns.
"""

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.enums import MedicineDoseStatus, MedicineFrequency, Role
from app.models.user import User
from app.security.deps import client_ip, forbid_phi_roles, get_db, require_roles
from app.services import medicines_service

router = APIRouter(prefix="/api/v1", tags=["medicines"])

resident_only = require_roles(Role.RESIDENT)
doctor_only = require_roles(Role.DOCTOR)
staff_only = require_roles(Role.DOCTOR, Role.NURSE, Role.OPS)


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    dose: str | None = Field(default=None, max_length=80)
    instructions: str | None = Field(default=None, max_length=240)
    frequency: MedicineFrequency
    times_of_day: list[str] = Field(default_factory=list, max_length=8)
    start_date: date
    end_date: date | None = None


class ScheduleOut(BaseModel):
    id: uuid.UUID
    resident_id: uuid.UUID
    project_id: uuid.UUID
    prescribed_by: uuid.UUID
    name: str
    dose: str | None
    instructions: str | None
    frequency: str
    times_of_day: list[str]
    start_date: date
    end_date: date | None
    active: bool
    created_at: datetime


class DoseLogIn(BaseModel):
    schedule_id: uuid.UUID
    scheduled_for: datetime
    status: MedicineDoseStatus
    notes: str | None = Field(default=None, max_length=240)


class DoseLogOut(BaseModel):
    id: uuid.UUID
    schedule_id: uuid.UUID
    resident_id: uuid.UUID
    scheduled_for: datetime
    status: str
    logged_at: datetime
    logged_by: uuid.UUID
    notes: str | None


class AdherenceTotalsOut(BaseModel):
    taken: int
    skipped: int
    missed: int
    scheduled_slots: int


class AdherenceScheduleOut(BaseModel):
    schedule_id: uuid.UUID
    name: str
    frequency: str
    active: bool
    taken: int
    skipped: int
    missed: int
    scheduled_slots: int


class AdherenceOut(BaseModel):
    resident_id: uuid.UUID
    window_days: int
    totals: AdherenceTotalsOut
    schedules: list[AdherenceScheduleOut]


def _schedule_input(body: ScheduleIn) -> medicines_service.ScheduleInput:
    return medicines_service.ScheduleInput(
        name=body.name,
        dose=body.dose,
        instructions=body.instructions,
        frequency=body.frequency,
        times_of_day=body.times_of_day,
        start_date=body.start_date,
        end_date=body.end_date,
    )


# --------------------------------------------------------------------------- #
# Resident self                                                                #
# --------------------------------------------------------------------------- #


@router.post("/me/medicines/schedules", response_model=ScheduleOut)
def create_own_schedule(
    body: ScheduleIn,
    request: Request,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> dict:
    from app.services.residents_service import get_resident_for_user

    resident = get_resident_for_user(session, user)
    return medicines_service.create_schedule(
        session,
        actor=user,
        resident_id=resident.id,
        data=_schedule_input(body),
        from_ip=client_ip(request),
    )


@router.get("/me/medicines/schedules", response_model=list[ScheduleOut])
def list_own_schedules(
    request: Request,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> list[dict]:
    return medicines_service.list_own_schedules(
        session, user=user, from_ip=client_ip(request)
    )


@router.post(
    "/me/medicines/schedules/{schedule_id}/deactivate",
    response_model=ScheduleOut,
)
def deactivate_own_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> dict:
    return medicines_service.deactivate_schedule(
        session, actor=user, schedule_id=schedule_id, from_ip=client_ip(request)
    )


@router.post("/me/medicines/doses", response_model=DoseLogOut)
def log_own_dose(
    body: DoseLogIn,
    request: Request,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> dict:
    return medicines_service.log_own_dose(
        session,
        user=user,
        data=medicines_service.DoseLogInput(
            schedule_id=body.schedule_id,
            scheduled_for=body.scheduled_for,
            status=body.status,
            notes=body.notes,
        ),
        from_ip=client_ip(request),
    )


# --------------------------------------------------------------------------- #
# Staff (doctor / nurse / ops)                                                 #
# --------------------------------------------------------------------------- #


@router.post(
    "/residents/{resident_id}/medicines/schedules",
    response_model=ScheduleOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def create_resident_schedule(
    resident_id: uuid.UUID,
    body: ScheduleIn,
    request: Request,
    actor: User = Depends(doctor_only),
    session: Session = Depends(get_db),
) -> dict:
    return medicines_service.create_schedule(
        session,
        actor=actor,
        resident_id=resident_id,
        data=_schedule_input(body),
        from_ip=client_ip(request),
    )


@router.get(
    "/residents/{resident_id}/medicines/schedules",
    response_model=list[ScheduleOut],
    dependencies=[Depends(forbid_phi_roles)],
)
def list_resident_schedules(
    resident_id: uuid.UUID,
    request: Request,
    actor: User = Depends(staff_only),
    session: Session = Depends(get_db),
) -> list[dict]:
    return medicines_service.list_resident_schedules_as_staff(
        session, actor=actor, resident_id=resident_id, from_ip=client_ip(request)
    )


@router.post(
    "/residents/{resident_id}/medicines/schedules/{schedule_id}/deactivate",
    response_model=ScheduleOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def deactivate_resident_schedule(
    resident_id: uuid.UUID,
    schedule_id: uuid.UUID,
    request: Request,
    actor: User = Depends(doctor_only),
    session: Session = Depends(get_db),
) -> dict:
    # resident_id is in the URL for routing/audit clarity; the service
    # validates that the schedule belongs to a resident in the actor's
    # project. We don't trust the path resident_id over the schedule row.
    return medicines_service.deactivate_schedule(
        session, actor=actor, schedule_id=schedule_id, from_ip=client_ip(request)
    )


@router.get(
    "/residents/{resident_id}/medicines/adherence",
    response_model=AdherenceOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def resident_adherence(
    resident_id: uuid.UUID,
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    actor: User = Depends(staff_only),
    session: Session = Depends(get_db),
) -> dict:
    return medicines_service.adherence_summary_for_staff(
        session,
        actor=actor,
        resident_id=resident_id,
        days=days,
        from_ip=client_ip(request),
    )
