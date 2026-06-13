from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel
from app.db.database import get_db
from app.models.models import TaskAssignment, User, ProjectMember, Notification
from app.core.deps import get_current_user
from app.services.audit_service import log_action
from app.services.notification_service import create_notification

router = APIRouter(prefix="/projects/{project_id}/assignments", tags=["Assignments"])

class AssignmentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    assigned_to: int
    team: Optional[str] = None
    milestone_num: Optional[int] = None
    priority: str = "Medium"
    due_date: Optional[datetime] = None
    remarks: Optional[str] = None

class AssignmentUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[datetime] = None
    remarks: Optional[str] = None
    description: Optional[str] = None

def _build_assignment(a: TaskAssignment, db: Session):
    assignee = db.query(User).filter_by(id=a.assigned_to).first()
    assigner = db.query(User).filter_by(id=a.assigned_by).first()
    return {
        "id":            a.id,
        "title":         a.title,
        "description":   a.description,
        "assigned_to":   a.assigned_to,
        "assigned_to_name": assignee.name if assignee else "—",
        "assigned_to_role": assignee.role if assignee else "—",
        "assigned_by":   a.assigned_by,
        "assigned_by_name": assigner.name if assigner else "—",
        "team":          a.team,
        "milestone_num": a.milestone_num,
        "priority":      a.priority,
        "status":        a.status,
        "due_date":      a.due_date,
        "completed_at":  a.completed_at,
        "created_at":    a.created_at,
        "remarks":       a.remarks,
        "project_id":    a.project_id,
    }

@router.get("")
def list_assignments(
    project_id: int,
    team: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    q = db.query(TaskAssignment).filter_by(project_id=project_id)
    # Non-admins only see their own assignments
    if current_user.role != "Admin":
        q = q.filter_by(assigned_to=current_user.id)
    if team:
        q = q.filter_by(team=team)
    if status:
        q = q.filter_by(status=status)
    assignments = q.order_by(TaskAssignment.created_at.desc()).all()
    return [_build_assignment(a, db) for a in assignments]

@router.get("/my")
def my_assignments(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    assignments = db.query(TaskAssignment).filter_by(
        project_id=project_id, assigned_to=current_user.id
    ).order_by(TaskAssignment.created_at.desc()).all()
    return [_build_assignment(a, db) for a in assignments]

@router.post("")
def create_assignment(
    project_id: int,
    payload: AssignmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in ("Admin", "Functional Consultant"):
        raise HTTPException(403, "Only Admin or Project Manager can assign tasks")

    assignee = db.query(User).filter_by(id=payload.assigned_to).first()
    if not assignee:
        raise HTTPException(404, "Assigned user not found")

    a = TaskAssignment(
        project_id=project_id,
        title=payload.title,
        description=payload.description,
        assigned_to=payload.assigned_to,
        assigned_by=current_user.id,
        team=payload.team or assignee.role,
        milestone_num=payload.milestone_num,
        priority=payload.priority,
        status="Not Started",
        due_date=payload.due_date,
        remarks=payload.remarks,
    )
    db.add(a)
    db.flush()

    # Notify assignee
    create_notification(
        db, project_id, "assignment",
        f"Task '{payload.title}' assigned to {assignee.name} by {current_user.name}",
        user_id=payload.assigned_to,
        email_to=assignee.email,
    )
    log_action(db, actor=current_user.name, action="assign_task",
               description=f"Assigned task '{payload.title}' to {assignee.name}",
               project_id=project_id, entity_type="assignment",
               entity_id=a.id, user_id=current_user.id)
    db.commit()
    db.refresh(a)
    return _build_assignment(a, db)

@router.patch("/{assignment_id}")
def update_assignment(
    project_id: int,
    assignment_id: int,
    payload: AssignmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    a = db.query(TaskAssignment).filter_by(id=assignment_id, project_id=project_id).first()
    if not a:
        raise HTTPException(404, "Assignment not found")

    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(a, k, v)

    if payload.status == "Completed" and not a.completed_at:
        a.completed_at = datetime.utcnow()
        create_notification(db, project_id, "completed",
                            f"Task '{a.title}' marked completed by {current_user.name}")

    log_action(db, actor=current_user.name, action="update_assignment",
               description=f"Updated assignment '{a.title}'",
               project_id=project_id, entity_type="assignment",
               entity_id=a.id, user_id=current_user.id)
    db.commit()
    db.refresh(a)
    return _build_assignment(a, db)

@router.delete("/{assignment_id}")
def delete_assignment(
    project_id: int,
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "Admin":
        raise HTTPException(403, "Only Admin can delete assignments")
    a = db.query(TaskAssignment).filter_by(id=assignment_id, project_id=project_id).first()
    if not a:
        raise HTTPException(404, "Assignment not found")
    db.delete(a)
    log_action(db, actor=current_user.name, action="delete_assignment",
               description=f"Deleted assignment '{a.title}'",
               project_id=project_id, user_id=current_user.id)
    db.commit()
    return {"status": "deleted"}

@router.get("/summary")
def assignment_summary(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    all_a = db.query(TaskAssignment).filter_by(project_id=project_id).all()
    return {
        "total":       len(all_a),
        "not_started": sum(1 for a in all_a if a.status == "Not Started"),
        "in_progress": sum(1 for a in all_a if a.status == "In Progress"),
        "completed":   sum(1 for a in all_a if a.status == "Completed"),
        "overdue":     sum(1 for a in all_a if a.due_date and a.due_date < datetime.utcnow() and a.status != "Completed"),
        "by_team": {
            "Functional Consultant": sum(1 for a in all_a if a.team == "Functional Consultant"),
            "Technical Team":        sum(1 for a in all_a if a.team == "Technical Team"),
        }
    }
