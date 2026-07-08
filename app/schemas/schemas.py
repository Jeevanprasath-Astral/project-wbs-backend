from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any
from datetime import datetime

# ── Auth ──────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    token: str
    user: dict

# ── Users ─────────────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str = "Functional Consultant"
    cost_rate: Optional[float] = 0.0

class UserOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    cost_rate: Optional[float] = 0.0
    model_config = {"from_attributes": True}

# ── Projects ──────────────────────────────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str
    client: Optional[str] = None
    business_unit: Optional[str] = None
    owner: Optional[str] = None
    location: Optional[str] = None
    project_type: str = "Implementation"
    project_category: Optional[str] = "Billable"
    functional_consultant: Optional[str] = None
    technical_lead: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    billing_amount: Optional[float] = 0.0

class ProjectOut(BaseModel):
    id: int
    name: str
    client: Optional[str]
    business_unit: Optional[str]
    owner: Optional[str]
    location: Optional[str]
    project_type: Optional[str]
    project_category: Optional[str] = "Billable"
    functional_consultant: Optional[str]
    technical_lead: Optional[str]
    description: Optional[str]
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    status: str
    progress: float
    budget: Optional[float] = 0.0
    billing_amount: Optional[float] = 0.0
    created_at: Optional[datetime]
    model_config = {"from_attributes": True}

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    client: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[float] = None
    billing_amount: Optional[float] = None
    project_category: Optional[str] = None

# ── Questions & Responses ─────────────────────────────────────────────────────
class QuestionOut(BaseModel):
    id: int
    num: int
    question_text: str
    input_type: str
    model_config = {"from_attributes": True}

class SubtaskOut(BaseModel):
    id: int
    num: int
    name: str
    is_format: bool
    input_type: str
    status: Optional[str] = "Not Started"
    assignee: Optional[str] = None
    reviewer: Optional[str] = None
    signed_off_at: Optional[datetime] = None
    response: Optional[str] = None
    questions: List[QuestionOut] = []
    responses: dict = {}
    model_config = {"from_attributes": True}

class TaskOut(BaseModel):
    id: int
    num: int
    name: str
    responsibility: Optional[str]
    status: Optional[str] = "Not Started"
    subtasks: List[SubtaskOut] = []
    model_config = {"from_attributes": True}

class MilestoneOut(BaseModel):
    id: int
    num: int
    name: str
    responsibility: Optional[str] = None
    status: str = "Not Started"
    progress: float = 0.0
    assignee: Optional[str] = None
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    reviewer: Optional[str] = None
    approver: Optional[str] = None
    signed_off_at: Optional[datetime] = None
    tasks: List[TaskOut] = []
    model_config = {"from_attributes": True}

# ── Response save ─────────────────────────────────────────────────────────────
class ResponseSave(BaseModel):
    question_id: Optional[int] = None
    subtask_id: Optional[int] = None
    value: str

# ── Sign-off ──────────────────────────────────────────────────────────────────
class SignOffRequest(BaseModel):
    subtask_id: Optional[int] = None

# ── Milestone update ──────────────────────────────────────────────────────────
class MilestoneUpdate(BaseModel):
    assignee: Optional[str] = None
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    status: Optional[str] = None
    remarks: Optional[str] = None

# ── Notifications ─────────────────────────────────────────────────────────────
class NotificationOut(BaseModel):
    id: int
    type: Optional[str]
    message: str
    email_to: Optional[str]
    email_sent: bool
    read: bool
    created_at: Optional[datetime]
    # Requirement 7(b): the Notifications tab groups by assignee and shows a
    # count rather than full message text by default — user_id/user_name
    # are needed to group server-side-resolved notifications by person.
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    model_config = {"from_attributes": True}

# ── Audit ─────────────────────────────────────────────────────────────────────
class AuditOut(BaseModel):
    id: int
    actor: Optional[str]
    entity_type: Optional[str]
    entity_id: Optional[int] = None
    action: Optional[str]
    description: Optional[str]
    old_value: Optional[str]
    new_value: Optional[str]
    created_at: Optional[datetime]
    # Requirement 4: Audit Trail must show Created By / Modified By separately.
    # created_by = actor of the earliest "create"-type log row for this
    # entity_type+entity_id; modified_by = this row's own actor.
    created_by: Optional[str] = None
    modified_by: Optional[str] = None
    model_config = {"from_attributes": True}

# ── Dashboard ─────────────────────────────────────────────────────────────────
class DashboardSummary(BaseModel):
    total: int
    completed: int
    in_progress: int
    overdue: int
    not_started: int
    progress: float
    done_tasks: int
    total_tasks: int
