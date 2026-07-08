"""Report Templates — reusable sets of report definitions that can be applied
to any Milestone to bulk-create independent MilestoneReport copies.

Copy-on-apply semantics: once a template is applied to a milestone, the
resulting reports are completely independent of the template and of each other.
Editing / deleting the template or reports on one milestone has no effect on
any other milestone."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel
from app.db.database import get_db
from app.models.models import ReportTemplate, ReportTemplateItem, MilestoneReport, User
from app.core.deps import get_current_user

router = APIRouter(prefix="/report-templates", tags=["Report Templates"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────
class TemplateItemIn(BaseModel):
    report_number: str
    report_name: str
    department: Optional[str] = None

class TemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    items: List[TemplateItemIn] = []

class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


# ── Helper ────────────────────────────────────────────────────────────────────
def _build(t: ReportTemplate):
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "created_by": t.created_by,
        "creator_name": t.creator.name if t.creator else None,
        "created_at": t.created_at,
        "items": [
            {
                "id": i.id,
                "report_number": i.report_number,
                "report_name": i.report_name,
                "department": i.department,
            }
            for i in t.items
        ],
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.get("")
def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    templates = db.query(ReportTemplate).order_by(ReportTemplate.name).all()
    return [_build(t) for t in templates]


@router.get("/{template_id}")
def get_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    t = db.query(ReportTemplate).filter_by(id=template_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    return _build(t)


@router.post("")
def create_template(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if db.query(ReportTemplate).filter_by(name=payload.name).first():
        raise HTTPException(400, f"A template named '{payload.name}' already exists")
    t = ReportTemplate(
        name=payload.name,
        description=payload.description,
        created_by=current_user.id,
    )
    db.add(t)
    db.flush()
    for item in payload.items:
        db.add(ReportTemplateItem(
            template_id=t.id,
            report_number=item.report_number,
            report_name=item.report_name,
            department=item.department,
        ))
    db.commit()
    db.refresh(t)
    return _build(t)


@router.delete("/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    t = db.query(ReportTemplate).filter_by(id=template_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    db.delete(t)
    db.commit()
    return {"status": "deleted"}


@router.post("/{template_id}/apply/{milestone_id}")
def apply_template(
    template_id: int,
    milestone_id: int,
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Copy all items from the template onto a milestone as independent
    MilestoneReport rows. Skips items whose report_number already exists on
    the milestone (idempotent — safe to apply twice)."""
    t = db.query(ReportTemplate).filter_by(id=template_id).first()
    if not t:
        raise HTTPException(404, "Template not found")

    created = 0
    skipped = 0
    for item in t.items:
        exists = db.query(MilestoneReport).filter_by(
            milestone_id=milestone_id, report_number=item.report_number
        ).first()
        if exists:
            skipped += 1
            continue
        db.add(MilestoneReport(
            milestone_id=milestone_id,
            project_id=project_id,
            report_number=item.report_number,
            report_name=item.report_name,
            department=item.department,
            status="Not Started",
        ))
        created += 1

    db.commit()
    return {"status": "ok", "created": created, "skipped": skipped}
