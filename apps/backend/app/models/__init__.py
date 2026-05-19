"""Model registry. Importing this module registers every table on
SQLModel.metadata and installs the audit-log immutability guard.
"""

from sqlalchemy import event

from app.models.audit import AuditLog
from app.models.auth import OtpCode, RefreshToken
from app.models.consent import Consent
from app.models.emergency_contact import EmergencyContact
from app.models.idempotency import IdempotencyKey
from app.models.medical_profile import MedicalProfile
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User

__all__ = [
    "AuditLog",
    "OtpCode",
    "RefreshToken",
    "Consent",
    "EmergencyContact",
    "IdempotencyKey",
    "MedicalProfile",
    "Project",
    "Resident",
    "User",
]


class AuditLogImmutableError(RuntimeError):
    """Raised if anything attempts to UPDATE or DELETE an audit_log row."""


def _block_audit_mutation(_mapper, _connection, _target) -> None:  # noqa: ANN001
    raise AuditLogImmutableError("audit_log is append-only")


# App-level append-only guard (also covered by a Postgres rule in the migration).
event.listen(AuditLog, "before_update", _block_audit_mutation)
event.listen(AuditLog, "before_delete", _block_audit_mutation)
