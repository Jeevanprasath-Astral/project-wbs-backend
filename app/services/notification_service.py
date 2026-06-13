from sqlalchemy.orm import Session
from app.models.models import Notification
from app.services.email_service import send_email

def create_notification(
    db: Session,
    project_id: int,
    type: str,
    message: str,
    user_id: int = None,
    email_to: str = None,
    send_now: bool = False,
    email_subject: str = None,
    email_body: str = None,
):
    notif = Notification(
        project_id=project_id,
        user_id=user_id,
        type=type,
        message=message,
        email_to=email_to,
        email_sent=False,
    )
    db.add(notif)
    db.flush()
    if send_now and email_to and email_subject and email_body:
        sent = send_email(email_to, email_subject, email_body)
        notif.email_sent = sent
    return notif
