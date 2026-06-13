from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime
from app.db.database import get_db
from app.models.models import Response, SubtaskStatus, Subtask, User, Question
from app.schemas.schemas import ResponseSave, SignOffRequest
from app.core.deps import get_current_user
from app.services.audit_service import log_action
from app.services.progress_service import recalculate_milestone_progress

router = APIRouter(prefix="/projects/{project_id}", tags=["Responses"])

@router.post("/responses")
def save_response(project_id: int, payload: ResponseSave,
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Find existing or create
    if payload.question_id:
        existing = db.query(Response).filter_by(project_id=project_id, question_id=payload.question_id).first()
        if existing:
            old_val = existing.value
            existing.value = payload.value
            existing.answered_by = current_user.id
        else:
            old_val = None
            existing = Response(project_id=project_id, question_id=payload.question_id,
                                value=payload.value, answered_by=current_user.id)
            db.add(existing)
        # Auto-update subtask status
        q = db.query(Question).filter_by(id=payload.question_id).first()
        if q:
            _update_subtask_status(db, project_id, q.subtask_id, current_user)
            subtask = db.query(Subtask).filter_by(id=q.subtask_id).first()
            if subtask and subtask.task and subtask.task.milestone:
                recalculate_milestone_progress(db, project_id, subtask.task.milestone.num)
        log_action(db, actor=current_user.name, action="response", description=f"Response saved for question {payload.question_id}",
                   project_id=project_id, entity_type="question", entity_id=payload.question_id,
                   old_value=old_val, new_value=payload.value, user_id=current_user.id)

    elif payload.subtask_id:
        existing = db.query(Response).filter_by(project_id=project_id, subtask_id=payload.subtask_id, question_id=None).first()
        if existing:
            existing.value = payload.value
            existing.answered_by = current_user.id
        else:
            existing = Response(project_id=project_id, subtask_id=payload.subtask_id,
                                value=payload.value, answered_by=current_user.id)
            db.add(existing)
        _update_subtask_status(db, project_id, payload.subtask_id, current_user)
        subtask = db.query(Subtask).filter_by(id=payload.subtask_id).first()
        if subtask and subtask.task and subtask.task.milestone:
            recalculate_milestone_progress(db, project_id, subtask.task.milestone.num)

    db.commit()
    return {"status": "saved"}

def _update_subtask_status(db: Session, project_id: int, subtask_id: int, user: User):
    ss = db.query(SubtaskStatus).filter_by(project_id=project_id, subtask_id=subtask_id).first()
    if not ss:
        subtask = db.query(Subtask).filter_by(id=subtask_id).first()
        if not subtask:
            return
        from app.models.models import ProjectMilestone
        pm = db.query(ProjectMilestone).filter_by(
            project_id=project_id,
            milestone_id=subtask.task.milestone_id
        ).first()
        ss = SubtaskStatus(
            project_id=project_id,
            project_milestone_id=pm.id if pm else None,
            subtask_id=subtask_id,
        )
        db.add(ss)
    if ss.status == "Not Started":
        ss.status = "In Progress"
        ss.actual_start = datetime.utcnow()
    db.flush()

@router.post("/signoffs")
def signoff_subtask(project_id: int, payload: SignOffRequest,
                    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ss = db.query(SubtaskStatus).filter_by(project_id=project_id, subtask_id=payload.subtask_id).first()
    if not ss:
        subtask = db.query(Subtask).filter_by(id=payload.subtask_id).first()
        from app.models.models import ProjectMilestone
        pm = db.query(ProjectMilestone).filter_by(project_id=project_id, milestone_id=subtask.task.milestone_id).first()
        ss = SubtaskStatus(project_id=project_id, project_milestone_id=pm.id if pm else None, subtask_id=payload.subtask_id)
        db.add(ss)
    ss.status = "Completed"
    ss.reviewer = current_user.name
    ss.signed_off_at = datetime.utcnow()
    ss.actual_end = datetime.utcnow()
    log_action(db, actor=current_user.name, action="signoff",
               description=f"Subtask {payload.subtask_id} signed off",
               project_id=project_id, entity_type="subtask", entity_id=payload.subtask_id,
               old_value="In Progress", new_value="Completed", user_id=current_user.id)
    subtask = db.query(Subtask).filter_by(id=payload.subtask_id).first()
    if subtask and subtask.task and subtask.task.milestone:
        recalculate_milestone_progress(db, project_id, subtask.task.milestone.num)
    db.commit()
    return {"status": "signed_off"}
