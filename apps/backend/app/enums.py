"""Python mirror of packages/shared-types/src/enums.ts (brief §10: no magic
strings). Values are stable — they hit the DB. A drift check between this file
and the TS source is added in a later slice.
"""

from enum import StrEnum


class Role(StrEnum):
    RESIDENT = "resident"
    FAMILY = "family"
    DOCTOR = "doctor"
    NURSE = "nurse"
    OPS = "ops"
    HOSPITAL = "hospital"
    SECURITY_DESK = "security_desk"
    BUILDER_ADMIN = "builder_admin"
    SUPER_ADMIN = "super_admin"


#: Roles that must NEVER see PHI (RBAC tests enforce this).
NON_PHI_ROLES: frozenset[Role] = frozenset({Role.BUILDER_ADMIN, Role.SECURITY_DESK})


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class AuditAction(StrEnum):
    OTP_REQUESTED = "otp_requested"
    OTP_VERIFY_FAILED = "otp_verify_failed"
    LOGIN_SUCCEEDED = "login_succeeded"
    TOKEN_REFRESHED = "token_refreshed"
    REFRESH_REUSE_BLOCKED = "refresh_reuse_blocked"
    LOGOUT = "logout"
