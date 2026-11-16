"""Legacy credit helpers — platform billing disabled; RunningHub bills upstream."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import User


def add_ledger(
    db: Session,
    *,
    user: User,
    entry_type: str,
    amount: int,
    label: str,
    project_name: str = "",
    job_id: UUID | None = None,
    idempotency_key: str | None = None,
    status: str = "已完成",
):
    _ = (db, user, entry_type, amount, label, project_name, job_id, idempotency_key, status)
    return None


def hold_credits(db: Session, user: User, amount: int, *, project_name: str, job_id: UUID, key: str) -> None:
    _ = (db, user, amount, project_name, job_id, key)


def consume_hold(db: Session, user: User, amount: int, *, project_name: str, job_id: UUID, key: str) -> None:
    _ = (db, user, amount, project_name, job_id, key)


def release_hold(db: Session, user: User, amount: int, *, project_name: str, job_id: UUID, key: str) -> None:
    _ = (db, user, amount, project_name, job_id, key)
