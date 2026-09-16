from sqlalchemy.orm import Session
from app.models.models import Project, ProjectMilestone, SubtaskStatus, Subtask, Task, Milestone
from datetime import datetime

def recalculate_milestone_progress(db: Session, project_id: int, milestone_num: int):
    pm = db.query(ProjectMilestone).filter_by(project_id=project_id, num=milestone_num).first()
    if not pm:
        return

    ms = db.query(Milestone).filter_by(num=milestone_num).first()
    if not ms:
        return

    subtask_ids = [s.id for t in ms.tasks for s in t.subtasks]
    if not subtask_ids:
        # Milestone template has no subtasks — if it was manually signed off, honour that
        if pm.status == "Completed" and pm.progress < 100.0:
            pm.progress = 100.0
            db.flush()
            recalculate_project_progress(db, project_id)
        return

    total = len(subtask_ids)
    done = db.query(SubtaskStatus).filter(
        SubtaskStatus.project_id == project_id,
        SubtaskStatus.subtask_id.in_(subtask_ids),
        SubtaskStatus.status == "Completed"
    ).count()

    # ── FIX 1: Cap progress at 100% ──────────────────────────────────────────
    raw = (done / total) * 100 if total else 0.0
    pm.progress = min(round(raw, 1), 100.0)

    # Determine status
    now = datetime.utcnow()
    if pm.progress >= 100:
        pm.progress = 100.0
        pm.status = "Completed"
        if not pm.actual_end:
            pm.actual_end = now
    elif pm.planned_end and now > pm.planned_end and pm.progress < 100:
        pm.status = "Overdue"
    elif pm.progress > 0:
        pm.status = "In Progress"
        if not pm.actual_start:
            pm.actual_start = now

    db.flush()
    recalculate_project_progress(db, project_id)

def recalculate_project_progress(db: Session, project_id: int):
    from app.models.models import CustomMilestone
    # Only average milestones the project has actively selected.
    # Inactive/unselected rows (all progress=0) must not dilute the average.
    active_nums = {
        cm.num for cm in db.query(CustomMilestone).filter_by(
            project_id=project_id, is_active=True
        ).all()
    }
    if active_nums:
        milestones = db.query(ProjectMilestone).filter(
            ProjectMilestone.project_id == project_id,
            ProjectMilestone.num.in_(active_nums)
        ).all()
    else:
        # No milestones selected yet — fall back to all standard rows
        milestones = db.query(ProjectMilestone).filter_by(project_id=project_id).all()
    if not milestones:
        return
    # Cap each milestone at 100 before averaging
    avg = sum(min(m.progress, 100.0) for m in milestones) / len(milestones)
    project = db.query(Project).filter_by(id=project_id).first()
    if project:
        project.progress = min(round(avg, 1), 100.0)
        completed = sum(1 for m in milestones if m.status == "Completed")
        in_prog   = sum(1 for m in milestones if m.status == "In Progress")
        if completed == len(milestones):
            project.status = "Completed"
        elif in_prog > 0 or completed > 0:
            project.status = "In Progress"
    db.flush()

def fix_existing_progress(db: Session):
    """Fix any existing milestones with progress > 100%."""
    from app.models.models import ProjectMilestone
    bad = db.query(ProjectMilestone).filter(ProjectMilestone.progress > 100).all()
    for pm in bad:
        pm.progress = 100.0
        if pm.status not in ("Completed", "Overdue"):
            pm.status = "Completed"
    if bad:
        db.commit()
    return len(bad)
