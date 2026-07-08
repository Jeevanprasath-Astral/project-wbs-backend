from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import List
from datetime import datetime
from app.db.database import get_db
from app.models.models import (ProjectMilestone, Milestone, Task, Subtask,
                                Question, Response, SubtaskStatus, User)
from app.schemas.schemas import MilestoneUpdate
from app.core.deps import get_current_user
from app.services.audit_service import log_action
from app.services.notification_service import create_notification
from app.services.progress_service import recalculate_milestone_progress

router = APIRouter(prefix="/projects/{project_id}", tags=["Milestones"])


def _build_milestone_detail(db: Session, project_id: int, ms_num: int, current_user: User):
    pm = db.query(ProjectMilestone).filter_by(
        project_id=project_id, num=ms_num
    ).first()

    ms = db.query(Milestone).options(
        joinedload(Milestone.tasks)
        .joinedload(Task.subtasks)
        .joinedload(Subtask.questions)
    ).filter_by(num=ms_num).first()

    if not ms or not pm:
        return None

    subtask_ids, question_ids = [], []
    for t in ms.tasks:
        for sub in t.subtasks:
            subtask_ids.append(sub.id)
            for q in sub.questions:
                question_ids.append(q.id)

    # subtask statuses
    ss_map = {}
    if subtask_ids:
        rows = db.query(SubtaskStatus).filter(
            SubtaskStatus.project_id == project_id,
            SubtaskStatus.subtask_id.in_(subtask_ids)
        ).all()
        ss_map = {r.subtask_id: r for r in rows}

    # responses — safe OR query
    resp_map = {}
    direct_map = {}

    conditions = []
    if question_ids:
        conditions.append(Response.question_id.in_(question_ids))
    if subtask_ids:
        conditions.append(Response.subtask_id.in_(subtask_ids))

    if conditions:
        all_responses = db.query(Response).filter(
            Response.project_id == project_id,
            or_(*conditions)
        ).all()
        for r in all_responses:
            if r.question_id:
                resp_map[r.question_id] = r.value
            elif r.subtask_id:
                direct_map[r.subtask_id] = r.value

    tasks_out = []
    for t in sorted(ms.tasks, key=lambda x: x.num or 0):
        task_status = "Not Started"
        subtasks_out = []

        for sub in sorted(t.subtasks, key=lambda x: x.num or 0):
            ss = ss_map.get(sub.id)
            sub_status = ss.status if ss else "Not Started"

            if sub_status == "In Progress":
                task_status = "In Progress"
            elif sub_status == "Completed" and task_status == "Not Started":
                task_status = "In Progress"

            subtasks_out.append({
                "id": sub.id,
                "num": sub.num,
                "name": sub.name,
                "is_format": sub.is_format,
                "input_type": sub.input_type,
                "status": sub_status,
                "assignee": ss.assignee if ss else None,
                "reviewer": ss.reviewer if ss else None,
                "signed_off_at": ss.signed_off_at if ss else None,
                "response": direct_map.get(sub.id),
                "questions": [
                    {
                        "id": q.id,
                        "num": q.num,
                        "question_text": q.question_text,
                        "input_type": q.input_type,
                    }
                    for q in sorted(sub.questions, key=lambda x: x.num or 0)
                ],
                "responses": {
                    q.id: {"value": resp_map[q.id]}
                    for q in sub.questions
                    if q.id in resp_map
                },
            })

        all_done = all(s["status"] == "Completed" for s in subtasks_out)
        if all_done and subtasks_out:
            task_status = "Completed"

        tasks_out.append({
            "id": t.id,
            "num": t.num,
            "name": t.name,
            "responsibility": t.responsibility,
            "status": task_status,
            "subtasks": subtasks_out,
        })

    return {
        "id": pm.id, "num": pm.num, "name": pm.name,
        "responsibility": ms.tasks[0].responsibility if ms.tasks else None,
        "status": pm.status, "progress": pm.progress,
        "assignee": pm.assignee,
        "planned_start": pm.planned_start, "planned_end": pm.planned_end,
        "actual_start": pm.actual_start, "actual_end": pm.actual_end,
        "reviewer": pm.reviewer, "approver": pm.approver,
        "signed_off_at": pm.signed_off_at,
        "tasks": tasks_out,
    }


@router.get("/milestones", response_model=List[dict])
def list_milestones(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Only return milestones the user has actually selected/confirmed for this
    # project (CustomMilestone). If none are selected yet, return nothing —
    # never fall back to the full standard 10-milestone catalog.
    from app.models.models import CustomMilestone
    selected_nums = {
        cm.num for cm in db.query(CustomMilestone).filter_by(
            project_id=project_id, is_active=True
        ).all()
    }
    if not selected_nums:
        return []
    pms = db.query(ProjectMilestone).filter(
        ProjectMilestone.project_id == project_id,
        ProjectMilestone.num.in_(selected_nums),
    ).order_by(ProjectMilestone.num).all()
    return [{"id": pm.id, "num": pm.num, "name": pm.name, "status": pm.status,
             "progress": pm.progress, "assignee": pm.assignee,
             "planned_start": pm.planned_start, "planned_end": pm.planned_end,
             "actual_start": pm.actual_start, "actual_end": pm.actual_end} for pm in pms]


@router.get("/milestones/{num}")
def get_milestone(project_id: int, num: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    data = _build_milestone_detail(db, project_id, num, current_user)
    if not data:
        raise HTTPException(404, "Milestone not found")
    return data


@router.patch("/milestones/{ms_id}")
def update_milestone(project_id: int, ms_id: int, payload: MilestoneUpdate,
                     db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    pm = db.query(ProjectMilestone).filter_by(id=ms_id, project_id=project_id).first()
    if not pm:
        raise HTTPException(404, "Milestone not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(pm, k, v)
    log_action(db, actor=current_user.name, action="update",
               description=f"Milestone {pm.num} updated",
               project_id=project_id, entity_type="milestone",
               entity_id=pm.id, user_id=current_user.id)
    db.commit()
    return {"status": "ok"}


@router.post("/milestones/{num}/signoff")
def signoff_milestone(project_id: int, num: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    pm = db.query(ProjectMilestone).filter_by(project_id=project_id, num=num).first()
    if not pm:
        raise HTTPException(404, "Milestone not found")
    pm.signed_off_at = datetime.utcnow()
    pm.reviewer = current_user.name
    pm.status = "Completed"
    create_notification(db, project_id, "completed",
                        f"Milestone {num:02d} '{pm.name}' signed off by {current_user.name}.")
    log_action(db, actor=current_user.name, action="signoff",
               description=f"Milestone {num} signed off",
               project_id=project_id, entity_type="milestone",
               entity_id=pm.id, old_value="In Progress",
               new_value="Completed", user_id=current_user.id)
    db.commit()
    return {"status": "ok"}
