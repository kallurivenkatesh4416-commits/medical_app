"""Demo seed: one project + one user per role so the dashboard/app can be
exercised end-to-end (PLAN.md Slice 2 demo proof: "login as each role").

Run after migrations:  python -m app.seed
Idempotent: existing phones are left untouched.
"""

from sqlmodel import Session, select

from app.db import engine
from app.enums import Role
from app.models.project import Project
from app.models.user import User

# Deterministic demo phones, one per role (E.164-ish, clearly fake).
SEED_PHONES: dict[Role, str] = {
    role: f"+1555000{idx:04d}" for idx, role in enumerate(Role, start=1)
}


def seed() -> None:
    with Session(engine) as session:
        project = session.exec(
            select(Project).where(Project.name == "Demo Residency")
        ).first()
        if project is None:
            project = Project(name="Demo Residency", enable_security_desk_alerts=True)
            session.add(project)
            session.commit()
            session.refresh(project)

        for role, phone in SEED_PHONES.items():
            exists = session.exec(select(User).where(User.phone == phone)).first()
            if exists:
                continue
            session.add(
                User(
                    # super_admin is global (no project binding).
                    project_id=None if role is Role.SUPER_ADMIN else project.id,
                    phone=phone,
                    role=role.value,
                    full_name=f"Demo {role.value}",
                )
            )
        session.commit()
    print(f"Seeded project + {len(SEED_PHONES)} role users.")


if __name__ == "__main__":
    seed()
