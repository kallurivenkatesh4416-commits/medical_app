"""Slice 10 aggregate admin KPIs and monthly exports.

The admin surface is explicitly non-PHI: project-level counts and durations
only. It never returns resident names, flat/villa numbers, case ids, record
ids, medicine names, or signed links.
"""

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from io import BytesIO, StringIO
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session, select
from xhtml2pdf import pisa

from app.enums import AuditAction, CaseStatus
from app.models.base import utcnow
from app.models.emergency import EmergencyCase
from app.models.medical_record import MedicalRecord
from app.models.resident import Resident
from app.models.user import User
from app.services import medicines_service
from app.services.audit import record_audit
from app.services.auth_service import AuthError

_TEMPLATE_DIR = Path(__file__).parent / "admin_templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


@dataclass(frozen=True)
class KpiWindow:
    start: datetime
    end: datetime
    label: str


def rolling_window(days: int) -> KpiWindow:
    if days <= 0 or days > 366:
        raise AuthError(422, "invalid_window", "Admin KPI window must be 1-366 days.")
    end = utcnow()
    return KpiWindow(start=end - timedelta(days=days), end=end, label=f"Last {days} days")


def monthly_window(month: str) -> KpiWindow:
    try:
        year, month_num = [int(part) for part in month.split("-", 1)]
        start_date = date(year, month_num, 1)
    except ValueError as exc:
        raise AuthError(422, "invalid_month", "month must be YYYY-MM.") from exc

    if start_date.month == 12:
        end_date = date(start_date.year + 1, 1, 1)
    else:
        end_date = date(start_date.year, start_date.month + 1, 1)
    return KpiWindow(
        start=datetime.combine(start_date, time.min),
        end=datetime.combine(end_date, time.min),
        label=start_date.strftime("%B %Y"),
    )


def admin_kpis(
    session: Session,
    *,
    actor: User,
    window: KpiWindow,
    from_ip: str | None,
) -> dict:
    summary = _aggregate(session, actor=actor, window=window)
    record_audit(
        session,
        action=AuditAction.ADMIN_KPI_READ,
        actor_user_id=actor.id,
        project_id=actor.project_id,
        resource_type="admin_kpi",
        from_ip=from_ip,
        purpose="admin.kpis",
        meta=_window_meta(window),
    )
    return summary


def monthly_export_context(
    session: Session,
    *,
    actor: User,
    month: str,
    export_format: str,
    from_ip: str | None,
) -> dict:
    window = monthly_window(month)
    summary = _aggregate(session, actor=actor, window=window)
    record_audit(
        session,
        action=AuditAction.ADMIN_EXPORT_GENERATED,
        actor_user_id=actor.id,
        project_id=actor.project_id,
        resource_type="admin_export",
        from_ip=from_ip,
        purpose="admin.export",
        meta={**_window_meta(window), "format": export_format},
    )
    return summary


def render_csv(summary: dict) -> bytes:
    out = StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["section", "metric", "value"])
    writer.writerow(["window", "label", summary["window_label"]])
    writer.writerow(["window", "start", summary["window_start"]])
    writer.writerow(["window", "end", summary["window_end"]])
    writer.writerow(["emergency", "total_cases", summary["emergency"]["total_cases"]])
    writer.writerow(["emergency", "active_cases", summary["emergency"]["active_cases"]])
    writer.writerow(["emergency", "closed_cases", summary["emergency"]["closed_cases"]])
    writer.writerow(
        ["emergency", "average_ack_seconds", summary["emergency"]["average_ack_seconds"]]
    )
    writer.writerow(
        [
            "emergency",
            "average_on_site_seconds",
            summary["emergency"]["average_on_site_seconds"],
        ]
    )
    writer.writerow(["residents", "onboarded", summary["residents"]["onboarded"]])
    writer.writerow(["records", "uploaded", summary["records"]["uploaded"]])
    for metric, value in summary["medicines"].items():
        writer.writerow(["medicines", metric, value])
    return out.getvalue().encode("utf-8")


def render_pdf(summary: dict) -> bytes:
    html = _jinja.get_template("monthly.html.j2").render(
        summary=summary,
        generated_at=utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )
    buf = BytesIO()
    status = pisa.CreatePDF(src=html, dest=buf, encoding="utf-8")
    if status.err:
        raise AuthError(502, "pdf_render_failed", "Could not render admin export PDF.")
    return buf.getvalue()


def export_filename(summary: dict, extension: str) -> str:
    month = summary["window_start"][:7]
    return f"admin-kpis-{month}.{extension}"


def _aggregate(session: Session, *, actor: User, window: KpiWindow) -> dict:
    if actor.project_id is None:
        raise AuthError(403, "project_required", "A project-scoped account is required.")
    project_id = actor.project_id
    now = _projection_now(window)

    cases = session.exec(
        select(EmergencyCase).where(
            EmergencyCase.project_id == project_id,
            EmergencyCase.alert_time >= window.start,
            EmergencyCase.alert_time < window.end,
        )
    ).all()
    ack_seconds = [
        (case.acknowledged_at - case.alert_time).total_seconds()
        for case in cases
        if case.acknowledged_at is not None
    ]
    on_site_seconds = [
        (case.on_site_at - case.alert_time).total_seconds()
        for case in cases
        if case.on_site_at is not None
    ]
    residents_onboarded = session.exec(
        select(Resident).where(
            Resident.project_id == project_id,
            Resident.created_at >= window.start,
            Resident.created_at < window.end,
        )
    ).all()
    records_uploaded = session.exec(
        select(MedicalRecord).where(
            MedicalRecord.project_id == project_id,
            MedicalRecord.created_at >= window.start,
            MedicalRecord.created_at < window.end,
            MedicalRecord.deleted_at.is_(None),  # type: ignore[union-attr]
        )
    ).all()

    return {
        "project_id": project_id,
        "window_label": window.label,
        "window_start": window.start.isoformat(),
        "window_end": window.end.isoformat(),
        "emergency": {
            "total_cases": len(cases),
            "active_cases": sum(1 for c in cases if c.status != CaseStatus.CLOSED.value),
            "closed_cases": sum(1 for c in cases if c.status == CaseStatus.CLOSED.value),
            "average_ack_seconds": _avg(ack_seconds),
            "average_on_site_seconds": _avg(on_site_seconds),
        },
        "residents": {"onboarded": len(residents_onboarded)},
        "records": {"uploaded": len(records_uploaded)},
        "medicines": medicines_service.project_adherence_summary(
            session,
            project_id=project_id,
            window_start=window.start,
            now=now,
        ),
    }


def _projection_now(window: KpiWindow) -> datetime:
    upper = min(window.end, utcnow())
    if upper <= window.start:
        return window.start
    # Window end is exclusive, while the Slice 9 slot projector is inclusive
    # by date. Step back one microsecond so monthly exports don't include the
    # first day of the next month.
    return upper - timedelta(microseconds=1)


def _window_meta(window: KpiWindow) -> dict:
    return {
        "window_label": window.label,
        "window_start": window.start.isoformat(),
        "window_end": window.end.isoformat(),
    }


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)
