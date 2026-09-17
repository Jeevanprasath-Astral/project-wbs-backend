"""
AXON Chatbot — Google Gemini-powered assistant with tool calling.

Architecture: LLM tool-using agent pattern
  1. User sends a message
  2. Gemini picks which tool(s) to call (or answers directly)
  3. Tool executor queries the database with the authenticated user's context
  4. Results are sent back to Gemini for a final natural-language answer

Role-based access is automatically enforced because every tool query runs
with the authenticated user's ID and role from the JWT — no extra logic needed.

Tools available (read-only, Phase 1):
  - list_my_projects         : projects the user is assigned to
  - get_project_details      : milestones, tasks, status for a project
  - get_my_hours             : the user's own logged work hours
  - get_team_workload        : all team members' hours for a date range
  - list_my_assignments      : tasks currently assigned to the user
  - get_dashboard_summary    : key KPIs for a project (progress, hours, overdue)
"""

import os
import json
import logging
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.database import get_db
from app.models.models import (
    User, Project, TaskAssignment, WorkHours,
    CustomMilestone, CustomTask,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chatbot"])

# ── Pydantic models ────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str          # "user" | "model"
    text: str

class ChatRequest(BaseModel):
    message: str
    conversation_history: list[ChatMessage] = []

class ChatResponse(BaseModel):
    reply: str
    tool_calls_made: list[str] = []    # for debug / transparency


# ── Gemini client (lazy init — avoids import-time crash if key is missing) ────

def _get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured on server")
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        return genai
    except ImportError:
        raise HTTPException(status_code=503, detail="google-generativeai package not installed")


# ── Tool definitions (sent to Gemini as function declarations) ─────────────────

TOOL_DECLARATIONS = [
    {
        "name": "list_my_projects",
        "description": (
            "List all projects the current user is assigned to or manages. "
            "Returns project name, status, category, start date, and project ID. "
            "Use this when the user asks about 'my projects', 'what projects am I on', "
            "or wants to explore their project list."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "description": "Optional filter: 'Active', 'Completed', 'On Hold'. Leave empty for all.",
                    "enum": ["Active", "Completed", "On Hold", ""],
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_project_details",
        "description": (
            "Get milestone and task progress details for a specific project. "
            "Returns milestones with status, planned/actual dates, iteration, "
            "and the tasks under each milestone. "
            "Use when the user asks about a specific project's progress, milestones, or timeline."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "The numeric ID of the project to look up.",
                },
                "project_name": {
                    "type": "string",
                    "description": "Project name to search by if ID is unknown. Partial match supported.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_my_hours",
        "description": (
            "Get work hours logged by the current user for a date range. "
            "Returns total hours, breakdown by project, and daily log entries. "
            "Use when the user asks 'how many hours have I logged', 'my timesheet', "
            "'what did I work on this week', etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format. Defaults to start of current week.",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format. Defaults to today.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_team_workload",
        "description": (
            "Get work hours logged by ALL team members for a date range. "
            "Returns per-person totals so you can see who is busy, idle, or overloaded. "
            "Use for questions about team utilization, who has capacity, workload distribution."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format. Defaults to start of current week.",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format. Defaults to today.",
                },
                "project_id": {
                    "type": "integer",
                    "description": "Optional: filter to a specific project. Leave out for all projects.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "list_my_assignments",
        "description": (
            "List tasks currently assigned to the current user across all projects. "
            "Returns task name, project, milestone, status, planned dates, and category. "
            "Use for 'what tasks do I have', 'what am I assigned to', 'my workload', "
            "'what's due soon', etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "description": "Optional filter by task status: 'Not Started', 'In Progress', 'Completed'.",
                },
                "project_id": {
                    "type": "integer",
                    "description": "Optional: filter to a specific project.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_dashboard_summary",
        "description": (
            "Get a high-level KPI summary for a project: overall progress %, "
            "total planned vs actual hours, count of overdue milestones, "
            "and number of open tasks. "
            "Use when the user asks for a project summary, overview, or health check."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "The numeric ID of the project.",
                },
                "project_name": {
                    "type": "string",
                    "description": "Project name to search by if ID is unknown.",
                },
            },
            "required": [],
        },
    },
]


# ── Tool executor ──────────────────────────────────────────────────────────────

def _week_start() -> str:
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()

def _today() -> str:
    return date.today().isoformat()


def _resolve_project(db: Session, project_id: int | None, project_name: str | None,
                     current_user: User) -> Project | None:
    """Find a project by ID or name, respecting the user's visible set."""
    q = db.query(Project).filter(Project.is_demo == False)

    # Non-admin users only see projects they are assigned to
    if current_user.role not in ("Admin", "FC Lead"):
        assigned_ids = [
            r[0] for r in db.query(TaskAssignment.project_id)
            .filter(TaskAssignment.assigned_to == current_user.name)
            .distinct().all()
        ]
        q = q.filter(Project.id.in_(assigned_ids))

    if project_id:
        return q.filter(Project.id == project_id).first()
    if project_name:
        return q.filter(Project.name.ilike(f"%{project_name}%")).first()
    return None


def execute_tool(tool_name: str, args: dict, db: Session, current_user: User) -> Any:
    """Route a Gemini function call to the right DB query and return a result dict."""

    # ── list_my_projects ──────────────────────────────────────────────────────
    if tool_name == "list_my_projects":
        status_filter = args.get("status_filter", "")

        if current_user.role in ("Admin", "FC Lead"):
            q = db.query(Project).filter(Project.is_demo == False)
        else:
            assigned_ids = [
                r[0] for r in db.query(TaskAssignment.project_id)
                .filter(TaskAssignment.assigned_to == current_user.name)
                .distinct().all()
            ]
            q = db.query(Project).filter(
                Project.id.in_(assigned_ids),
                Project.is_demo == False
            )

        if status_filter:
            q = q.filter(Project.status == status_filter)

        projects = q.order_by(Project.name).all()
        return {
            "count": len(projects),
            "projects": [
                {
                    "id": p.id,
                    "name": p.name,
                    "status": p.status,
                    "category": getattr(p, "project_category", "Billable"),
                    "client": getattr(p, "client_name", ""),
                    "start_date": str(p.start_date) if p.start_date else None,
                }
                for p in projects
            ],
        }

    # ── get_project_details ───────────────────────────────────────────────────
    elif tool_name == "get_project_details":
        project = _resolve_project(db, args.get("project_id"), args.get("project_name"), current_user)
        if not project:
            return {"error": "Project not found or you do not have access to it."}

        milestones = (
            db.query(CustomMilestone)
            .filter(CustomMilestone.project_id == project.id)
            .order_by(CustomMilestone.num)
            .all()
        )

        ms_list = []
        for ms in milestones:
            tasks = (
                db.query(CustomTask)
                .filter(CustomTask.milestone_id == ms.id)
                .order_by(CustomTask.num)
                .all()
            )
            ms_list.append({
                "num": ms.num,
                "name": ms.name,
                "status": getattr(ms, "status", "Not Started"),
                "planned_start": str(ms.planned_start.date()) if ms.planned_start else None,
                "planned_end": str(ms.planned_end.date()) if ms.planned_end else None,
                "actual_start": str(ms.actual_start.date()) if ms.actual_start else None,
                "actual_end": str(ms.actual_end.date()) if ms.actual_end else None,
                "iteration": getattr(ms, "iteration", 1),
                "tasks": [
                    {
                        "num": t.num,
                        "name": t.name,
                        "status": getattr(t, "status", "Not Started"),
                        "assignee": getattr(t, "assignee", ""),
                        "estimated_hours": getattr(t, "estimated_hours", 0),
                    }
                    for t in tasks
                ],
            })

        return {
            "project_id": project.id,
            "project_name": project.name,
            "status": project.status,
            "milestones": ms_list,
        }

    # ── get_my_hours ──────────────────────────────────────────────────────────
    elif tool_name == "get_my_hours":
        date_from = args.get("date_from") or _week_start()
        date_to = args.get("date_to") or _today()

        rows = (
            db.query(WorkHours)
            .filter(
                WorkHours.user_id == current_user.id,
                WorkHours.date >= date_from,
                WorkHours.date <= date_to,
            )
            .order_by(WorkHours.date)
            .all()
        )

        total_hours = sum(r.hours_spent or 0 for r in rows)

        # Group by project
        by_project: dict[str, float] = {}
        entries = []
        for r in rows:
            proj = db.query(Project.name).filter(Project.id == r.project_id).scalar() or "General"
            by_project[proj] = by_project.get(proj, 0) + (r.hours_spent or 0)
            entries.append({
                "date": str(r.date),
                "project": proj,
                "hours": r.hours_spent,
                "work_type": getattr(r, "work_type", "Billable"),
                "description": r.description or "",
            })

        return {
            "user": current_user.name,
            "date_from": date_from,
            "date_to": date_to,
            "total_hours": round(total_hours, 2),
            "by_project": [{"project": k, "hours": round(v, 2)} for k, v in by_project.items()],
            "entries": entries,
        }

    # ── get_team_workload ─────────────────────────────────────────────────────
    elif tool_name == "get_team_workload":
        date_from = args.get("date_from") or _week_start()
        date_to = args.get("date_to") or _today()
        project_id = args.get("project_id")

        q = (
            db.query(
                User.name,
                User.role,
                func.coalesce(func.sum(WorkHours.hours_spent), 0).label("total_hours"),
            )
            .outerjoin(WorkHours, (WorkHours.user_id == User.id) &
                       (WorkHours.date >= date_from) & (WorkHours.date <= date_to))
            .filter(User.is_active == True, User.is_demo == False)
        )

        if project_id:
            q = q.filter(
                (WorkHours.project_id == project_id) | (WorkHours.project_id == None)
            )

        results = q.group_by(User.name, User.role).order_by(User.name).all()

        return {
            "date_from": date_from,
            "date_to": date_to,
            "team": [
                {"name": r.name, "role": r.role, "hours": round(float(r.total_hours), 2)}
                for r in results
            ],
        }

    # ── list_my_assignments ───────────────────────────────────────────────────
    elif tool_name == "list_my_assignments":
        status_filter = args.get("status_filter", "")
        project_id = args.get("project_id")

        q = (
            db.query(TaskAssignment)
            .filter(TaskAssignment.assigned_to == current_user.name)
        )
        if status_filter:
            q = q.filter(TaskAssignment.status == status_filter)
        if project_id:
            q = q.filter(TaskAssignment.project_id == project_id)

        assignments = q.order_by(TaskAssignment.planned_end).limit(50).all()

        result = []
        for a in assignments:
            proj_name = db.query(Project.name).filter(Project.id == a.project_id).scalar() or "General"
            task_name = ""
            if a.custom_task_id:
                task_name = db.query(CustomTask.name).filter(CustomTask.id == a.custom_task_id).scalar() or ""
            result.append({
                "task": a.task_name or task_name,
                "project": proj_name,
                "status": a.status or "Not Started",
                "category": getattr(a, "category", ""),
                "planned_start": str(a.planned_start.date()) if a.planned_start else None,
                "planned_end": str(a.planned_end.date()) if a.planned_end else None,
                "actual_start": str(a.actual_start.date()) if a.actual_start else None,
                "actual_end": str(a.actual_end.date()) if a.actual_end else None,
            })

        return {
            "user": current_user.name,
            "count": len(result),
            "assignments": result,
        }

    # ── get_dashboard_summary ─────────────────────────────────────────────────
    elif tool_name == "get_dashboard_summary":
        project = _resolve_project(db, args.get("project_id"), args.get("project_name"), current_user)
        if not project:
            return {"error": "Project not found or you do not have access to it."}

        milestones = (
            db.query(CustomMilestone)
            .filter(CustomMilestone.project_id == project.id)
            .all()
        )

        total_ms = len(milestones)
        completed_ms = sum(1 for m in milestones if getattr(m, "status", "") == "Completed")
        progress_pct = round((completed_ms / total_ms * 100) if total_ms else 0, 1)

        today = date.today()
        overdue = sum(
            1 for m in milestones
            if getattr(m, "status", "") not in ("Completed",)
            and m.planned_end and m.planned_end.date() < today
        )

        planned_hours = (
            db.query(func.coalesce(func.sum(CustomTask.estimated_hours), 0))
            .join(CustomMilestone, CustomTask.milestone_id == CustomMilestone.id)
            .filter(CustomMilestone.project_id == project.id)
            .scalar()
        )

        actual_hours = (
            db.query(func.coalesce(func.sum(WorkHours.hours_spent), 0))
            .filter(WorkHours.project_id == project.id)
            .scalar()
        )

        open_tasks = (
            db.query(func.count(TaskAssignment.id))
            .filter(
                TaskAssignment.project_id == project.id,
                TaskAssignment.status != "Completed",
            )
            .scalar()
        )

        return {
            "project_id": project.id,
            "project_name": project.name,
            "status": project.status,
            "progress_pct": progress_pct,
            "milestones_total": total_ms,
            "milestones_completed": completed_ms,
            "overdue_milestones": overdue,
            "planned_hours": round(float(planned_hours), 1),
            "actual_hours": round(float(actual_hours), 1),
            "open_tasks": open_tasks,
        }

    else:
        return {"error": f"Unknown tool: {tool_name}"}


# ── System prompt ──────────────────────────────────────────────────────────────

def _build_system_prompt(user: User) -> str:
    today = date.today().strftime("%A, %d %B %Y")
    return f"""You are AXON Assistant, an intelligent AI helper embedded inside the AXON project management application used by Astral Business Consulting.

Today's date: {today}
Current user: {user.name}
User role: {user.role}

You help the team with project management insights. You can:
- Look up the user's projects, milestones, tasks, and assignments
- Check how many hours the user or the team has logged
- Summarise project progress and health
- Answer questions about workload, deadlines, and schedules

Always be concise, professional, and helpful. When showing data, use clear formatting.
When you don't have access to a tool that would answer the question, say so honestly.
Never invent data — always use the tools to fetch real information.

If the user asks about something outside project management (e.g. general coding help), you can still assist but clarify you are primarily an AXON project assistant.
"""


# ── Main chat endpoint ─────────────────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    genai = _get_gemini_client()

    # Build tool config for Gemini
    tools = [{"function_declarations": TOOL_DECLARATIONS}]

    # Convert conversation history to Gemini format
    history = [
        {"role": msg.role, "parts": [{"text": msg.text}]}
        for msg in payload.conversation_history
    ]

    # Start chat session with history
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        tools=tools,
        system_instruction=_build_system_prompt(current_user),
    )

    try:
        chat_session = model.start_chat(history=history)
        response = chat_session.send_message(payload.message)
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")

    tool_calls_made = []

    # Agentic loop — Gemini may request multiple tool calls in sequence
    MAX_ITERATIONS = 6
    for _ in range(MAX_ITERATIONS):
        # Check if Gemini wants to call any tools
        tool_call_parts = []
        for part in response.parts:
            if hasattr(part, "function_call") and part.function_call.name:
                tool_call_parts.append(part.function_call)

        if not tool_call_parts:
            break  # No more tool calls — Gemini has its final answer

        # Execute all requested tool calls
        function_responses = []
        for fc in tool_call_parts:
            tool_name = fc.name
            args = dict(fc.args) if fc.args else {}
            tool_calls_made.append(tool_name)

            logger.info(f"Tool call: {tool_name}({args}) by user {current_user.id}")

            try:
                result = execute_tool(tool_name, args, db, current_user)
            except Exception as e:
                logger.error(f"Tool execution error [{tool_name}]: {e}")
                result = {"error": str(e)}

            function_responses.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=tool_name,
                        response={"result": json.dumps(result, default=str)},
                    )
                )
            )

        # Send tool results back to Gemini
        try:
            response = chat_session.send_message(function_responses)
        except Exception as e:
            logger.error(f"Gemini tool-response error: {e}")
            raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")

    # Extract the final text reply
    reply_text = ""
    for part in response.parts:
        if hasattr(part, "text") and part.text:
            reply_text += part.text

    if not reply_text:
        reply_text = "I'm sorry, I couldn't generate a response. Please try again."

    return ChatResponse(reply=reply_text, tool_calls_made=tool_calls_made)
