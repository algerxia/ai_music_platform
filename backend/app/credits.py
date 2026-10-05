from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import CreditLedger, User


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
) -> CreditLedger:
    if idempotency_key:
        existing = (
            db.query(CreditLedger)
            .filter(CreditLedger.idempotency_key == idempotency_key)
            .one_or_none()
        )
        if existing:
            return existing
    user.credit_balance += amount
    if user.credit_balance < 0:
        raise HTTPException(status_code=409, detail="积分不足")
    entry = CreditLedger(
        user_id=user.id,
        entry_type=entry_type,
        amount=amount,
        balance_after=user.credit_balance,
        label=label,
        project_name=project_name,
        job_id=job_id,
        idempotency_key=idempotency_key,
        status=status,
    )
    db.add(entry)
    return entry


def hold_credits(db: Session, user: User, amount: int, *, project_name: str, job_id: UUID, key: str) -> None:
    if user.credit_balance < amount:
        raise HTTPException(status_code=402, detail=f"积分不足，本次需要 {amount} 积分")
    add_ledger(
        db,
        user=user,
        entry_type="hold",
        amount=-amount,
        label="生成预留",
        project_name=project_name,
        job_id=job_id,
        idempotency_key=f"hold-{key}",
        status="预留中",
    )


def consume_hold(db: Session, user: User, amount: int, *, project_name: str, job_id: UUID, key: str) -> None:
    add_ledger(
        db,
        user=user,
        entry_type="consume",
        amount=0,
        label=f"生成结算（已扣 {amount}）",
        project_name=project_name,
        job_id=job_id,
        idempotency_key=f"consume-{key}",
        status="已完成",
    )


def release_hold(db: Session, user: User, amount: int, *, project_name: str, job_id: UUID, key: str) -> None:
    add_ledger(
        db,
        user=user,
        entry_type="release",
        amount=amount,
        label="生成失败退回",
        project_name=project_name,
        job_id=job_id,
        idempotency_key=f"release-{key}",
        status="已退回",
    )
