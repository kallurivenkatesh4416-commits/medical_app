"""Model registry. Importing this module registers every table on
SQLModel.metadata and installs the audit-log immutability guard.
"""

from sqlalchemy import event

from app.models.audit import AuditLog
from app.models.auth import OtpCode, RefreshToken
from app.models.project import Project
from app.models.user import User

__all__ = ["AuditLog", "OtpCode", "RefreshToken", "Project", "User"]


class AuditLogImmutableError(RuntimeError):
    """Raised if anything attempts to UPDATE or DELETE an audit_log row."""


def _block_audit_mutation(_mapper, _connection, _target) -> None:  # noqa: ANN001
    raise AuditLogImmutableError("audit_log is append-only")


# App-level append-only guard (also covered by a Postgres rule in the migration).
event.listen(AuditLog, "before_update", _block_audit_mutation)
event.listen(AuditLog, "before_delete", _block_audit_mutation)
