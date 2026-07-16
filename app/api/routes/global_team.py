from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from app.db.database import get_db
from app.models.models import (User, ProjectMember, Project, TaskAssignment, Team,
                               CustomRole, AssignmentCategory, WorkHours, AuditLog,
                               Notification, LeaveRequest, Permission, PasswordResetToken)
from app.core.deps import get_current_user
from app.core.permissions import is_team_manager
from app.core.security import hash_password
from app.services.audit_service import log_action
from app.services.email_service import send_welcome_email
from app.core.config import settings

router = APIRouter(prefix="/global/team", tags=["Global Team"])

ROLE_PERMISSIONS = {
    "Admin": ["all"],
    "Project Manager": ["create_requirements","manage_milestones","assign_tasks","delete_assignments","set_timelines","view_reports","view_dashboard","manage_costs","create_projects"],
    "FC Lead": ["create_requirements","manage_milestones","assign_tasks","delete_assignments","set_timelines","view_reports","view_dashboard","manage_costs"],
    "TC Lead": ["manage_dev_tasks","update_status","assign_technical","delete_assignments","set_timelines","view_reports","view_dashboard","manage_costs"],
    # Associate replaces Functional Consultant + Technical Team
    "Associate": ["create_requirements","manage_milestones","assign_tasks","set_timelines","view_reports","view_dashboard"],
    # Legacy roles — same permissions as Associate
    "Functional Consultant": ["create_requirements","manage_milestones","assign_tasks","set_timelines","view_reports","view_dashboard"],
    "Technical Team": ["manage_dev_tasks","update_status","view_reports","view_dashboard"],
    "HR": ["add_members","manage_teams","manage_holidays","approve_leave","view_reports","view_dashboard"],
    "Client": ["view_dashboard","view_reports"],
}

class UserCreate(BaseModel):
    name: str
    email: str
    role: str
    password: str = "wbs123"
    department: Optional[str] = None
    phone: Optional[str] = None
    team_id: Optional[int] = None

class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    department: Optional[str] = None
    phone: Optional[str] = None
    team_id: Optional[int] = None
    cost_rate: Optional[float] = None

class TeamCreate(BaseModel):
    name: str
    description: Optional[str] = None

class TeamUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None

def _build_user(u: User, db: Session):
    projects = db.query(ProjectMember).filter_by(user_id=u.id).count()
    tasks = db.query(TaskAssignment).filter_by(assigned_to=u.id).count()
    completed = db.query(TaskAssignment).filter_by(assigned_to=u.id, status="Completed").count()
    return {
        "id": u.id, "name": u.name, "email": u.email,
        "role": u.role, "is_active": u.is_active,
        "created_at": u.created_at,
        "team_id": u.team_id,
        "team_name": u.team.name if u.team else None,
        "cost_rate": u.cost_rate or 0.0,
        "project_count": projects,
        "task_count": tasks,
        "completed_tasks": completed,
        "completion_rate": round(completed/tasks*100, 1) if tasks else 0,
        "permissions": ROLE_PERMISSIONS.get(u.role, []),
    }

def _build_team(t: Team, db: Session):
    members = db.query(User).filter_by(team_id=t.id).all()
    return {
        "id": t.id, "name": t.name, "description": t.description,
        "is_active": t.is_active, "created_at": t.created_at,
        "member_count": len(members),
        "members": [{"id": m.id, "name": m.name, "role": m.role, "email": m.email} for m in members],
    }


# ── Team CRUD (master source for Team Creation) ───────────────────────────────
@router.get("/teams")
def list_teams(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    teams = db.query(Team).order_by(Team.name).all()
    return [_build_team(t, db) for t in teams]

@router.post("/teams")
def create_team(payload: TeamCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can create teams")
    if db.query(Team).filter_by(name=payload.name).first():
        raise HTTPException(400, "A team with this name already exists")
    t = Team(name=payload.name, description=payload.description, is_active=True)
    db.add(t); db.commit(); db.refresh(t)
    log_action(db, actor=current_user.name, action="create_team",
               description=f"Created team {t.name}", user_id=current_user.id)
    return _build_team(t, db)

@router.patch("/teams/{team_id}")
def update_team(team_id: int, payload: TeamUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can update teams")
    t = db.query(Team).filter_by(id=team_id).first()
    if not t: raise HTTPException(404, "Team not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(t, k, v)
    db.commit(); db.refresh(t)
    return _build_team(t, db)

@router.delete("/teams/{team_id}")
def delete_team(team_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can delete teams")
    t = db.query(Team).filter_by(id=team_id).first()
    if not t: raise HTTPException(404, "Team not found")
    db.query(User).filter_by(team_id=team_id).update({User.team_id: None})
    db.delete(t); db.commit()
    return {"status": "deleted"}

@router.get("")
def list_all_users(
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    team_id: Optional[int] = None,
    project_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    q = db.query(User)
    if role: q = q.filter(User.role == role)
    if is_active is not None: q = q.filter(User.is_active == is_active)
    if team_id is not None: q = q.filter(User.team_id == team_id)
    if project_id is not None:
        # Only members assigned to this specific project (via ProjectMember) —
        # keeps the Global Hub Team page in sync with whatever a project's
        # Team tab shows once a member is added there.
        member_user_ids = {
            m.user_id for m in db.query(ProjectMember).filter_by(project_id=project_id).all()
        }
        q = q.filter(User.id.in_(member_user_ids)) if member_user_ids else q.filter(False)
    users = q.order_by(User.name).all()
    return [_build_user(u, db) for u in users]

@router.get("/stats")
def team_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    all_users = db.query(User).all()
    teams = db.query(Team).all()
    return {
        "total": len(all_users),
        "active": sum(1 for u in all_users if u.is_active),
        "inactive": sum(1 for u in all_users if not u.is_active),
        "by_role": {
            "Admin": sum(1 for u in all_users if u.role == "Admin"),
            "FC Lead": sum(1 for u in all_users if u.role == "FC Lead"),
            "TC Lead": sum(1 for u in all_users if u.role == "TC Lead"),
            "Functional Consultant": sum(1 for u in all_users if u.role == "Functional Consultant"),
            "Technical Team": sum(1 for u in all_users if u.role == "Technical Team"),
            "HR": sum(1 for u in all_users if u.role == "HR"),
            "Client": sum(1 for u in all_users if u.role == "Client"),
        },
        "team_count": len(teams),
        "by_team": {t.name: sum(1 for u in all_users if u.team_id == t.id) for t in teams},
    }

@router.post("")
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can create users")
    if db.query(User).filter_by(email=payload.email).first():
        raise HTTPException(400, "Email already registered")
    u = User(
        name=payload.name, email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role, is_active=True, team_id=payload.team_id
    )
    db.add(u); db.commit(); db.refresh(u)
    log_action(db, actor=current_user.name, action="create_user",
               description=f"Created user {u.name} ({u.role})", user_id=current_user.id)
    # Send welcome email with credentials
    try:
        send_welcome_email(
            to=u.email,
            name=u.name,
            temp_password=payload.password,
            app_url=settings.FRONTEND_URL
        )
    except Exception as _e:
        import logging as _log
        _log.error(f"Welcome email failed for {u.email}: {_e}")
    return _build_user(u, db)

@router.patch("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can update users")
    u = db.query(User).filter_by(id=user_id).first()
    if not u: raise HTTPException(404, "User not found")
    if payload.name:     u.name = payload.name
    if payload.role:     u.role = payload.role
    if payload.password: u.password_hash = hash_password(payload.password)
    if payload.is_active is not None: u.is_active = payload.is_active
    if payload.team_id is not None: u.team_id = payload.team_id
    if payload.cost_rate is not None: u.cost_rate = payload.cost_rate
    log_action(db, actor=current_user.name, action="update_user",
               description=f"Updated user {u.name}", user_id=current_user.id)
    db.commit(); db.refresh(u)
    return _build_user(u, db)

@router.delete("/{user_id}")
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can deactivate users")
    u = db.query(User).filter_by(id=user_id).first()
    if not u: raise HTTPException(404, "User not found")
    if u.id == current_user.id:
        raise HTTPException(400, "Cannot deactivate yourself")
    u.is_active = False
    log_action(db, actor=current_user.name, action="deactivate_user",
               description=f"Deactivated user {u.name}", user_id=current_user.id)
    db.commit()
    return {"status": "deactivated"}

@router.get("/{user_id}/impact")
def user_removal_impact(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a summary of what will be removed when permanently deleting this user.
    Called by the confirmation dialog before the actual DELETE so the admin can
    see exactly what will be affected."""
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can view member impact")
    u = db.query(User).filter_by(id=user_id).first()
    if not u:
        raise HTTPException(404, "User not found")
    if u.id == current_user.id:
        raise HTTPException(400, "Cannot remove yourself")

    # Projects this user belongs to
    memberships = db.query(ProjectMember).filter_by(user_id=user_id).all()
    project_ids = [m.project_id for m in memberships]
    project_names = [
        p.name
        for p in db.query(Project).filter(Project.id.in_(project_ids)).all()
    ] if project_ids else []

    # Task assignments
    total_assignments = db.query(TaskAssignment).filter(
        (TaskAssignment.assigned_to == user_id) | (TaskAssignment.assigned_by == user_id)
    ).count()
    open_assignments = db.query(TaskAssignment).filter(
        TaskAssignment.assigned_to == user_id,
        TaskAssignment.status.notin_(["Completed"]),
    ).count()

    # Work hours entries
    wh_count = db.query(WorkHours).filter_by(user_id=user_id).count()

    return {
        "user": {"id": u.id, "name": u.name, "email": u.email, "role": u.role},
        "project_count": len(project_names),
        "project_names": project_names,
        "total_assignments": total_assignments,
        "open_assignments": open_assignments,
        "work_hours_entries": wh_count,
    }


@router.delete("/{user_id}/remove")
def remove_user_permanently(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently delete a user and clean up all their FK dependencies.

    Dependency handling (in order, to satisfy NOT NULL FKs):
    1. ProjectMember rows (user_id NOT NULL)             → DELETE
    2. TaskAssignment rows (assigned_to/by NOT NULL)     → DELETE
    3. WorkHours rows (user_id NOT NULL)                 → DELETE
    4. AuditLog rows (user_id NULLABLE)                  → SET NULL (preserve history)
    5. Notification rows (user_id NULLABLE)              → DELETE
    6. LeaveRequest rows (user_id NOT NULL)              → DELETE
    7. Permission rows (user_id NOT NULL)                → DELETE
    8. PasswordResetToken rows (user_id NOT NULL)        → DELETE
    9. User record                                       → DELETE
    """
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can permanently remove members")
    u = db.query(User).filter_by(id=user_id).first()
    if not u:
        raise HTTPException(404, "User not found")
    if u.id == current_user.id:
        raise HTTPException(400, "Cannot remove yourself")

    user_name = u.name
    user_email = u.email

    # 1. Remove from all project memberships
    db.query(ProjectMember).filter_by(user_id=user_id).delete(synchronize_session=False)

    # 2. Delete all task assignments (both assigned_to and assigned_by — same rows)
    db.query(TaskAssignment).filter(
        (TaskAssignment.assigned_to == user_id) | (TaskAssignment.assigned_by == user_id)
    ).delete(synchronize_session=False)

    # 3. Delete work hour entries
    db.query(WorkHours).filter_by(user_id=user_id).delete(synchronize_session=False)

    # 4. Null out audit log references (preserve historical log entries)
    db.query(AuditLog).filter_by(user_id=user_id).update(
        {AuditLog.user_id: None}, synchronize_session=False
    )

    # 5. Delete notifications
    db.query(Notification).filter_by(user_id=user_id).delete(synchronize_session=False)

    # 6. Delete leave requests
    db.query(LeaveRequest).filter_by(user_id=user_id).delete(synchronize_session=False)

    # 7. Delete permissions
    db.query(Permission).filter_by(user_id=user_id).delete(synchronize_session=False)

    # 8. Delete password reset tokens (keyed by email, not user_id)
    db.query(PasswordResetToken).filter_by(email=user_email).delete(synchronize_session=False)

    # 9. Delete the user record
    db.delete(u)

    log_action(
        db,
        actor=current_user.name,
        action="remove_user",
        description=f"Permanently removed user '{user_name}' ({user_email})",
        user_id=current_user.id,
    )
    db.commit()
    return {"status": "removed", "message": f"{user_name} has been permanently removed"}


@router.get("/{user_id}/projects")
def user_projects(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    members = db.query(ProjectMember).filter_by(user_id=user_id).all()
    result = []
    for m in members:
        p = db.query(Project).filter_by(id=m.project_id).first()
        if p:
            result.append({"id": p.id, "name": p.name, "status": p.status, "progress": p.progress, "role": m.role})
    return result

# ── Custom Roles ───────────────────────────────────────────────────────────────
# Separate router so the prefix doesn't conflict with /global/team/{user_id}
_roles_router = APIRouter(prefix="/global/custom-roles", tags=["Custom Roles"])

@_roles_router.get("")
def list_custom_roles(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [{"id": r.id, "name": r.name} for r in db.query(CustomRole).order_by(CustomRole.name).all()]

@_roles_router.post("")
def create_custom_role(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can create roles")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Role name required")
    if db.query(CustomRole).filter_by(name=name).first():
        raise HTTPException(400, "Role already exists")
    r = CustomRole(name=name)
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "name": r.name}

# ── Assignment Categories ──────────────────────────────────────────────────────
_cats_router = APIRouter(prefix="/global/assignment-categories", tags=["Assignment Categories"])

@_cats_router.get("")
def list_assignment_categories(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [{"id": c.id, "name": c.name} for c in db.query(AssignmentCategory).order_by(AssignmentCategory.name).all()]

@_cats_router.post("")
def create_assignment_category(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Category name required")
    if db.query(AssignmentCategory).filter_by(name=name).first():
        raise HTTPException(400, "Category already exists")
    c = AssignmentCategory(name=name)
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "name": c.name}
