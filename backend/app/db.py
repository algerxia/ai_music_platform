from collections.abc import Generator

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base, Consent, CreditLedger, User
from app.passwords import hash_password


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

_ALTERS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS locale VARCHAR(16) DEFAULT 'zh-CN'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) DEFAULT 'Asia/Shanghai'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS daw VARCHAR(64) DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS role_label VARCHAR(64) DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS time_signature VARCHAR(16) DEFAULT '4/4'",
    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT ''",
    "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS credits_charged INTEGER DEFAULT 0",
    "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS capability_schema_version VARCHAR(32) DEFAULT ''",
    "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS progress_stage VARCHAR(64) DEFAULT 'queued'",
    "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS error_code VARCHAR(64) DEFAULT ''",
    "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN DEFAULT FALSE",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS note TEXT DEFAULT ''",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS asset_type VARCHAR(32) DEFAULT 'audio'",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS terms_version VARCHAR(16) DEFAULT '1.0'",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS file_size_bytes INTEGER DEFAULT 0",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS parent_asset_id UUID",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_hash VARCHAR(128) DEFAULT ''",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expires TIMESTAMPTZ",
]


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for stmt in _ALTERS:
            conn.execute(text(stmt))
    with SessionLocal() as db:
        email = settings.bootstrap_admin_email.strip().lower()
        user = db.scalar(select(User).where(User.email == email))
        if user:
            return
        user = User(
            email=email,
            display_name=settings.bootstrap_admin_name,
            password_hash=hash_password(settings.bootstrap_admin_password),
            plan_code="creator",
            credit_balance=settings.free_trial_credits,
        )
        db.add(user)
        db.flush()
        db.add(
            Consent(
                user_id=user.id,
                consent_type="terms",
                document_version="1.0",
                accepted=True,
            )
        )
        db.add(
            Consent(
                user_id=user.id,
                consent_type="privacy",
                document_version="1.0",
                accepted=True,
            )
        )
        db.add(
            CreditLedger(
                user_id=user.id,
                entry_type="grant",
                amount=settings.free_trial_credits,
                balance_after=settings.free_trial_credits,
                label="首次免费额度",
                project_name="Creator 方案",
                status="已发放",
                idempotency_key=f"grant-signup-{user.id}",
            )
        )
        db.commit()
