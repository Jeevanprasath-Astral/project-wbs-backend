from sqlalchemy.orm import Session
from app.models.models import AuditLog

def log_action(
    db: Session,
    actor: str,
    action: str,
    description: str,
    project_id: int = None,
    entity_type: str = None,
    entity_id: int = None,
    old_value: str = None,
    new_value: str = None,
    user_id: int = None,
):
    entry = AuditLog(
        project_id=project_id,
        user_id=user_id,
        actor=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        description=description,
        old_value=old_value,
        new_value=new_value,
    )
    db.add(entry)
    db.flush()
    return entry
