from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.db.database import get_db
from app.models.models import Project, ProjectMilestone, Milestone, User, ProjectMember
from app.schemas.schemas import ProjectCreate, ProjectOut, ProjectUpdate
from app.core.deps import get_current_user
from app.core.permissions import is_team_manager, can_create_project
from app.services.audit_service import log_action
from app.core.security import hash_password
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/projects", tags=["Projects"])

class AddMemberRequest(BaseModel):
    name: str
    email: str
    role: str
    password: Optional[str] = "wbs123"

class NewUserRequest(BaseModel):
    name: str
    email: str
    role: str
    password: str = "wbs123"

def _init_project_milestones(db: Session, project: Project):
    milestones = db.query(Milestone).order_by(Milestone.num).all()
    for ms in milestones:
        pm = ProjectMilestone(
            project_id=project.id, milestone_id=ms.id,
            num=ms.num, name=ms.name, status="Not Started", progress=0.0,
        )
        db.add(pm)
    db.flush()

@router.get("", response_model=List[ProjectOut])
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # All authenticated users see all projects.
    # Role-based permissions within each project are enforced at the action level.
    return db.query(Project).order_by(Project.created_at.desc()).all()

@router.post("", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not can_create_project(current_user):
        raise HTTPException(403, "Only Admin and Project Manager can create projects")
    project = Project(**payload.model_dump(), created_by=current_user.id, status="Not Started", progress=0.0)
    db.add(project)
    db.flush()
    _init_project_milestones(db, project)
    db.add(ProjectMember(project_id=project.id, user_id=current_user.id, role=current_user.role))
    log_action(db, actor=current_user.name, action="create",
               description=f"Project '{project.name}' created",
               project_id=project.id, entity_type="project",
               entity_id=project.id, user_id=current_user.id)
    db.commit()
    db.refresh(project)
    return project

@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    p = db.query(Project).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    return p

@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, payload: ProjectUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    p = db.query(Project).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    log_action(db, actor=current_user.name, action="update",
               description="Project updated", project_id=project_id,
               entity_type="project", entity_id=project_id, user_id=current_user.id)
    db.commit()
    db.refresh(p)
    return p

@router.get("/{project_id}/team")
def get_team(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    members = db.query(ProjectMember).filter_by(project_id=project_id).all()
    result = []
    for m in members:
        user = db.query(User).filter_by(id=m.user_id).first()
        if user:
            result.append({
                "member_id": m.id,
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "task_count": 0,
                "is_active": user.is_active,
            })
    return result

@router.get("/{project_id}/all-users")
def get_all_users(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get all users not already in this project."""
    existing_ids = [m.user_id for m in db.query(ProjectMember).filter_by(project_id=project_id).all()]
    users = db.query(User).filter(User.is_active == True, ~User.id.in_(existing_ids)).all()
    return [{"id": u.id, "name": u.name, "email": u.email, "role": u.role} for u in users]

@router.post("/{project_id}/team/add-existing")
def add_existing_member(project_id: int, payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Add an existing user to the project."""
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can add team members")
    user_id = payload.get("user_id")
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    existing = db.query(ProjectMember).filter_by(project_id=project_id, user_id=user_id).first()
    if existing:
        raise HTTPException(400, "User already in project")
    db.add(ProjectMember(project_id=project_id, user_id=user_id, role=user.role))
    log_action(db, actor=current_user.name, action="add_member",
               description=f"Added {user.name} to project",
               project_id=project_id, user_id=current_user.id)
    db.commit()
    return {"status": "ok", "message": f"{user.name} added to project"}

@router.post("/{project_id}/team/add-new")
def add_new_member(project_id: int, payload: AddMemberRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Create a new user and add them to the project."""
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can add team members")
    if db.query(User).filter_by(email=payload.email).first():
        raise HTTPException(400, "Email already registered")
    user = User(
        name=payload.name, email=payload.email,
        password_hash=hash_password(payload.password or "wbs123"),
        role=payload.role, is_active=True
    )
    db.add(user)
    db.flush()
    db.add(ProjectMember(project_id=project_id, user_id=user.id, role=user.role))
    log_action(db, actor=current_user.name, action="add_member",
               description=f"Created and added {user.name} to project",
               project_id=project_id, user_id=current_user.id)
    db.commit()
    return {"status": "ok", "message": f"{user.name} created and added to project"}

@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Delete a project and all its related data (Admin only)."""
    if current_user.role != "Admin":
        raise HTTPException(403, "Only Admin can delete projects")
    p = db.query(Project).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    log_action(db, actor=current_user.name, action="delete",
               description=f"Project '{p.name}' deleted",
               project_id=project_id, entity_type="project",
               entity_id=project_id, user_id=current_user.id)
    db.delete(p)
    db.commit()
    return {"status": "ok", "message": f"Project '{p.name}' deleted"}

@router.delete("/{project_id}/team/{member_id}")
def remove_member(project_id: int, member_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Remove a member from the project."""
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can remove team members")
    member = db.query(ProjectMember).filter_by(id=member_id, project_id=project_id).first()
    if not member:
        raise HTTPException(404, "Member not found")
    user = db.query(User).filter_by(id=member.user_id).first()
    db.delete(member)
    log_action(db, actor=current_user.name, action="remove_member",
               description=f"Removed {user.name if user else 'user'} from project",
               project_id=project_id, user_id=current_user.id)
    db.commit()
    return {"status": "ok"}
