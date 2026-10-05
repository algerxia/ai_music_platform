from app.models import AuditEvent


def record(db, *, actor_id, action: str, resource_type: str, resource_id: str = "", metadata: dict | None = None):
    db.add(
        AuditEvent(
            actor_user_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id or ""),
            metadata_json=metadata or {},
        )
    )
