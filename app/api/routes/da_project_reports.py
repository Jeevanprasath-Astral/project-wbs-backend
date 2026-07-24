"""
DA Project Report Master — CRUD for the project-level report catalogue
used exclusively by Data Analytics projects.

Each DA project has one master list of reports (ProjectReport rows in
project_reports_da table). These are then distributed across Milestones
and Batches via MilestoneReport rows.

Routes:
  GET    /projects/{pid}/da-project-reports            — list all reports for project
  POST   /projects/{pid}/da-project-reports            — create a new report
  PATCH  /projects/{pid}/da-project-reports/{rid}      — update report name/dept
  DELETE /projects/{pid}/da-project-reports/{rid}      — delete (only if not in use)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional, List
from app.db.database import get_db
from app.models.models import ProjectReport, MilestoneReport, Project
from app.api.routes.auth import get_current_user
from app.models.models import User

router = APIRouter(tags=["DA Project Reports"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class DAReportCreate(BaseModel):
    report_number: str
    report_name: str
    department: Optional[str] = None


class DAReportUpdate(BaseModel):
    report_number: Optional[str] = None
    report_name: Optional[str] = None
    department: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build(r: ProjectReport, milestone_count: int = 0) -> dict:
    return {
        "id":            r.id,
        "project_id":    r.project_id,
        "report_number": r.report_number,
        "report_name":   r.report_name,
        "department":    r.department,
        "milestone_count": milestone_count,   # how many milestones use this report
        "created_at":    r.created_at.isoformat() if r.created_at else None,
    }


def _get_project_or_404(pid: int, db: Session) -> Project:
    p = db.query(Project).filter_by(id=pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return p


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/projects/{pid}/da-project-reports")
def list_da_reports(
    pid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all reports in the master catalogue for a DA project."""
    _get_project_or_404(pid, db)
    reports = (
        db.query(ProjectReport)
        .filter_by(project_id=pid)
        .order_by(ProjectReport.report_number)
        .all()
    )
    # Count milestone usages in one query
    usage_rows = (
        db.query(MilestoneReport.project_report_id, func.count(MilestoneReport.id))
        .filter(MilestoneReport.project_id == pid, MilestoneReport.project_report_id.isnot(None))
        .group_by(MilestoneReport.project_report_id)
        .all()
    )
    usage = {row[0]: row[1] for row in usage_rows}
    return [_build(r, usage.get(r.id, 0)) for r in reports]


@router.post("/projects/{pid}/da-project-reports", status_code=201)
def create_da_report(
    pid: int,
    payload: DAReportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a new report to the DA project master catalogue."""
    _get_project_or_404(pid, db)
    # Check for duplicate report_number within this project
    existing = db.query(ProjectReport).filter_by(
        project_id=pid, report_number=payload.report_number
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Report number '{payload.report_number}' already exists in this project."
        )
    r = ProjectReport(
        project_id=pid,
        report_number=payload.report_number.strip(),
        report_name=payload.report_name.strip(),
        department=payload.department.strip() if payload.department else None,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _build(r)


@router.patch("/projects/{pid}/da-project-reports/{rid}")
def update_da_report(
    pid: int,
    rid: int,
    payload: DAReportUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update name / department of a report in the master catalogue.
    Changing report_number is allowed but must stay unique within the project.
    """
    r = db.query(ProjectReport).filter_by(id=rid, project_id=pid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Report not found")

    if payload.report_number is not None and payload.report_number != r.report_number:
        clash = db.query(ProjectReport).filter_by(
            project_id=pid, report_number=payload.report_number
        ).first()
        if clash:
            raise HTTPException(
                status_code=400,
                detail=f"Report number '{payload.report_number}' already exists in this project."
            )
        r.report_number = payload.report_number.strip()
        # Cascade: update all MilestoneReports that reference this master report
        milestone_reports = db.query(MilestoneReport).filter_by(project_report_id=rid).all()
        for mr in milestone_reports:
            mr.report_number = payload.report_number.strip()

    if payload.report_name is not None:
        r.report_name = payload.report_name.strip()
        # Cascade name to milestone reports
        milestone_reports = db.query(MilestoneReport).filter_by(project_report_id=rid).all()
        for mr in milestone_reports:
            mr.report_name = payload.report_name.strip()

    if payload.department is not None:
        r.department = payload.department.strip() if payload.department else None

    db.commit()
    db.refresh(r)
    usage = db.query(func.count(MilestoneReport.id)).filter_by(project_report_id=rid).scalar() or 0
    return _build(r, usage)


@router.delete("/projects/{pid}/da-project-reports/{rid}", status_code=204)
def delete_da_report(
    pid: int,
    rid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a master report. Fails if it is currently assigned to any milestone batch."""
    r = db.query(ProjectReport).filter_by(id=rid, project_id=pid).first()
    if not r:
        raise HTTPException(status_code=404, detail="Report not found")
    in_use = db.query(func.count(MilestoneReport.id)).filter_by(project_report_id=rid).scalar() or 0
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete: report is assigned to {in_use} milestone batch(es). Remove those assignments first."
        )
    db.delete(r)
    db.commit()
