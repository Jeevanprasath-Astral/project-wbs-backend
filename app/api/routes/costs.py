import uuid
from datetime import date as date_type
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.models.models import ProjectCost, Project, User
from app.core.deps import get_current_user
from app.core.permissions import is_elevated
from app.utils.cloudinary_helper import upload_file, delete_file, build_url

router = APIRouter(prefix="/projects/{project_id}/costs", tags=["Cost Management"])

COST_CATEGORIES = [
    "Travel", "Accommodation", "Software & Licensing", "Hardware & Equipment",
    "Training", "Consulting & Outsourcing", "Communication",
    "Indirect / Overhead",
    "Miscellaneous", "Other",
]


def _require_admin(current_user: User):
    if not is_elevated(current_user):
        raise HTTPException(status_code=403, detail="Admin or Lead access required for Cost Management changes")


def _serialize(c: ProjectCost) -> dict:
    return {
        "id": c.id,
        "project_id": c.project_id,
        "date": c.date.isoformat() if c.date else None,
        "particulars": c.particulars,
        "category": c.category,
        "cost": c.cost,
        "attachment_filename": c.attachment_filename,
        "attachment_original_filename": c.attachment_original_filename,
        "attachment_url": build_url(c.attachment_filename) if c.attachment_filename else None,
        "created_by": c.created_by,
        "created_by_name": c.creator.name if c.creator else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _get_project_or_404(project_id: int, db: Session) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _save_attachment(file: Optional[UploadFile]) -> tuple[Optional[str], Optional[str]]:
    """Upload file to Cloudinary. Returns (public_id, original_filename)."""
    if not file or not file.filename:
        return None, None
    public_id = f"wbs/costs/{uuid.uuid4().hex}"
    file_bytes = await file.read()
    result = upload_file(file_bytes, public_id)
    return result["public_id"], file.filename


def _delete_attachment(public_id: Optional[str]):
    """Delete file from Cloudinary by public_id."""
    if public_id:
        delete_file(public_id)


@router.get("/categories")
def list_categories(current_user: User = Depends(get_current_user)):
    return COST_CATEGORIES


@router.get("/summary")
def cost_summary(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_project_or_404(project_id, db)
    costs = db.query(ProjectCost).filter(ProjectCost.project_id == project_id).all()
    total_actual = sum(c.cost for c in costs)
    budget = project.budget or 0.0

    by_category = {}
    for c in costs:
        by_category.setdefault(c.category, 0.0)
        by_category[c.category] += c.cost

    return {
        "budget": budget,
        "total_actual_cost": total_actual,
        "remaining": budget - total_actual,
        "utilization_pct": round((total_actual / budget * 100), 1) if budget else None,
        "is_over_budget": budget > 0 and total_actual > budget,
        "entry_count": len(costs),
        "by_category": [{"category": k, "total": v} for k, v in sorted(by_category.items(), key=lambda x: -x[1])],
    }


class BudgetUpdate(BaseModel):
    budget: float


@router.patch("/budget")
def set_budget(
    project_id: int,
    payload: BudgetUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    project = _get_project_or_404(project_id, db)
    project.budget = payload.budget
    db.commit()
    return {"budget": project.budget}


@router.get("")
def list_costs(
    project_id: int,
    category: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_project_or_404(project_id, db)
    q = db.query(ProjectCost).filter(ProjectCost.project_id == project_id)
    if category:
        q = q.filter(ProjectCost.category == category)
    if date_from:
        q = q.filter(ProjectCost.date >= date_type.fromisoformat(date_from))
    if date_to:
        q = q.filter(ProjectCost.date <= date_type.fromisoformat(date_to))
    costs = q.order_by(ProjectCost.date.desc(), ProjectCost.id.desc()).all()
    return [_serialize(c) for c in costs]


@router.post("")
async def create_cost(
    project_id: int,
    date: str = Form(...),
    particulars: str = Form(...),
    category: str = Form(...),
    cost: float = Form(...),
    attachment: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    _get_project_or_404(project_id, db)
    stored_name, original_name = await _save_attachment(attachment)
    c = ProjectCost(
        project_id=project_id,
        date=date_type.fromisoformat(date),
        particulars=particulars,
        category=category,
        cost=cost,
        attachment_filename=stored_name,
        attachment_original_filename=original_name,
        created_by=current_user.id,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _serialize(c)


@router.patch("/{cost_id}")
async def update_cost(
    project_id: int,
    cost_id: int,
    date: Optional[str] = Form(None),
    particulars: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    cost: Optional[float] = Form(None),
    attachment: Optional[UploadFile] = File(None),
    remove_attachment: Optional[bool] = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    c = db.query(ProjectCost).filter(ProjectCost.id == cost_id, ProjectCost.project_id == project_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Cost entry not found")

    if date is not None:
        c.date = date_type.fromisoformat(date)
    if particulars is not None:
        c.particulars = particulars
    if category is not None:
        c.category = category
    if cost is not None:
        c.cost = cost

    if attachment and attachment.filename:
        _delete_attachment(c.attachment_filename)
        stored_name, original_name = await _save_attachment(attachment)
        c.attachment_filename = stored_name
        c.attachment_original_filename = original_name
    elif remove_attachment:
        _delete_attachment(c.attachment_filename)
        c.attachment_filename = None
        c.attachment_original_filename = None

    db.commit()
    db.refresh(c)
    return _serialize(c)


@router.delete("/{cost_id}")
def delete_cost(
    project_id: int,
    cost_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    c = db.query(ProjectCost).filter(ProjectCost.id == cost_id, ProjectCost.project_id == project_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Cost entry not found")
    _delete_attachment(c.attachment_filename)
    db.delete(c)
    db.commit()
    return {"status": "deleted"}
