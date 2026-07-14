from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Date, Float, ForeignKey, Enum, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base
import enum

class UserRole(str, enum.Enum):
    admin = "Admin"
    functional_consultant = "Functional Consultant"
    technical_team = "Technical Team"
    client = "Client"
    # User Access Management — new distinct role values (NOT flags layered
    # on top of the existing roles above). See app/core/permissions.py for
    # what each one can access.
    fc_lead = "FC Lead"
    tc_lead = "TC Lead"
    hr = "HR"

class StatusEnum(str, enum.Enum):
    not_started = "Not Started"
    in_progress = "In Progress"
    completed = "Completed"
    on_hold = "On Hold"
    overdue = "Overdue"

class NotifType(str, enum.Enum):
    assignment = "assignment"
    started = "started"
    completed = "completed"
    reminder = "reminder"
    overdue = "overdue"

# ── Teams ────────────────────────────────────────────────────────────────────
class Team(Base):
    __tablename__ = "teams"
    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(150), nullable=False, unique=True)
    description = Column(Text)
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    members     = relationship("User", back_populates="team")

# ── Password Reset Tokens ────────────────────────────────────────────────────
class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id         = Column(Integer, primary_key=True, index=True)
    email      = Column(String(200), nullable=False, index=True)
    token      = Column(String(100), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# ── Users ────────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(120), nullable=False)
    email         = Column(String(200), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    role          = Column(String(50), default=UserRole.functional_consultant)
    team_id       = Column(Integer, ForeignKey("teams.id"), nullable=True)
    # Profitability Report — hourly cost rate for this user (salary/contract
    # rate expressed per hour). Used to compute Manpower Cost = hours × rate.
    cost_rate     = Column(Float, nullable=True, default=0.0)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    projects      = relationship("ProjectMember", back_populates="user")
    team          = relationship("Team", back_populates="members")

# ── Projects ─────────────────────────────────────────────────────────────────
class Project(Base):
    __tablename__ = "projects"
    id                     = Column(Integer, primary_key=True, index=True)
    name                   = Column(String(200), nullable=False)
    client                 = Column(String(200))
    business_unit          = Column(String(200))
    owner                  = Column(String(200))
    location               = Column(String(200))
    project_type           = Column(String(100), default="Implementation")
    # Billing classification for the My Projects grouping view.
    # Values: Billable | Non-Billable | R&D  (default: Billable)
    project_category       = Column(String(30), default="Billable")
    functional_consultant  = Column(String(200))
    technical_lead         = Column(String(200))
    description            = Column(Text)
    start_date             = Column(DateTime)
    end_date               = Column(DateTime)
    status                 = Column(String(50), default="Not Started")
    progress               = Column(Float, default=0.0)
    # Cost Management — overall approved budget for the project; compared
    # against the sum of ProjectCost.cost rows for the Budget vs Actual Cost
    # summary. Null/0 = no budget set yet.
    budget                 = Column(Float, default=0.0)
    # Profitability Report — total contract/billing amount agreed with the
    # client. Distinct from budget (internal cost ceiling). Used as the Revenue
    # figure: Net Profit = billing_amount - Total Cost.
    billing_amount         = Column(Float, nullable=True, default=0.0)
    created_by             = Column(Integer, ForeignKey("users.id"))
    created_at             = Column(DateTime(timezone=True), server_default=func.now())
    updated_at             = Column(DateTime(timezone=True), onupdate=func.now())
    milestones             = relationship("ProjectMilestone", back_populates="project", cascade="all, delete-orphan")
    members                = relationship("ProjectMember",    back_populates="project", cascade="all, delete-orphan")
    notifications          = relationship("Notification",     back_populates="project", cascade="all, delete-orphan")
    audit_logs             = relationship("AuditLog",         back_populates="project", cascade="all, delete-orphan")
    costs                  = relationship("ProjectCost",      back_populates="project", cascade="all, delete-orphan")

class ProjectMember(Base):
    __tablename__ = "project_members"
    id         = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    user_id    = Column(Integer, ForeignKey("users.id"),    nullable=False)
    role       = Column(String(100))
    project    = relationship("Project",  back_populates="members")
    user       = relationship("User",     back_populates="projects")

# ── Milestone template (master, seeded once) ─────────────────────────────────
class Milestone(Base):
    __tablename__ = "milestones"
    id     = Column(Integer, primary_key=True)
    num    = Column(Integer, nullable=False)
    name   = Column(String(200), nullable=False)
    tasks  = relationship("Task", back_populates="milestone", cascade="all, delete-orphan")

class Task(Base):
    __tablename__ = "tasks"
    id              = Column(Integer, primary_key=True)
    milestone_id    = Column(Integer, ForeignKey("milestones.id"), nullable=False)
    num             = Column(Integer)
    name            = Column(String(200), nullable=False)
    responsibility  = Column(String(200))
    milestone       = relationship("Milestone", back_populates="tasks")
    subtasks        = relationship("Subtask",   back_populates="task", cascade="all, delete-orphan")

class Subtask(Base):
    __tablename__ = "subtasks"
    id         = Column(Integer, primary_key=True)
    task_id    = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    num        = Column(Integer)
    name       = Column(String(300), nullable=False)
    is_format  = Column(Boolean, default=True)
    input_type = Column(String(50), default="text")
    task       = relationship("Task",      back_populates="subtasks")
    questions  = relationship("Question",  back_populates="subtask", cascade="all, delete-orphan")

class Question(Base):
    __tablename__ = "questions"
    id            = Column(Integer, primary_key=True)
    subtask_id    = Column(Integer, ForeignKey("subtasks.id"), nullable=False)
    num           = Column(Integer)
    question_text = Column(Text, nullable=False)
    input_type    = Column(String(50), default="text")
    subtask       = relationship("Subtask",  back_populates="questions")
    responses     = relationship("Response", back_populates="question", cascade="all, delete-orphan")

# ── Project milestone instances (per project) ─────────────────────────────────
class ProjectMilestone(Base):
    __tablename__ = "project_milestones"
    id              = Column(Integer, primary_key=True)
    project_id      = Column(Integer, ForeignKey("projects.id"),  nullable=False)
    milestone_id    = Column(Integer, ForeignKey("milestones.id"), nullable=False)
    num             = Column(Integer)
    name            = Column(String(200))
    status          = Column(String(50), default="Not Started")
    progress        = Column(Float, default=0.0)
    assignee        = Column(String(200))
    planned_start   = Column(DateTime)
    planned_end     = Column(DateTime)
    actual_start    = Column(DateTime)
    actual_end      = Column(DateTime)
    reviewer        = Column(String(200))
    approver        = Column(String(200))
    signed_off_at   = Column(DateTime)
    remarks         = Column(Text)
    project         = relationship("Project",         back_populates="milestones")
    subtask_statuses = relationship("SubtaskStatus",  back_populates="project_milestone", cascade="all, delete-orphan")

class SubtaskStatus(Base):
    __tablename__ = "subtask_statuses"
    id                   = Column(Integer, primary_key=True)
    project_id           = Column(Integer, ForeignKey("projects.id"),           nullable=False)
    project_milestone_id = Column(Integer, ForeignKey("project_milestones.id"), nullable=False)
    subtask_id           = Column(Integer, ForeignKey("subtasks.id"),            nullable=False)
    status               = Column(String(50), default="Not Started")
    assignee             = Column(String(200))
    planned_start        = Column(DateTime)
    planned_end          = Column(DateTime)
    actual_start         = Column(DateTime)
    actual_end           = Column(DateTime)
    reviewer             = Column(String(200))
    signed_off_at        = Column(DateTime)
    remarks              = Column(Text)
    project_milestone    = relationship("ProjectMilestone", back_populates="subtask_statuses")

# ── Responses ─────────────────────────────────────────────────────────────────
class Response(Base):
    __tablename__ = "responses"
    id          = Column(Integer, primary_key=True)
    project_id  = Column(Integer, ForeignKey("projects.id"),  nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=True)
    subtask_id  = Column(Integer, ForeignKey("subtasks.id"),  nullable=True)
    value       = Column(Text)
    answered_by = Column(Integer, ForeignKey("users.id"))
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    question    = relationship("Question", back_populates="responses")

# ── Notifications ─────────────────────────────────────────────────────────────
class Notification(Base):
    __tablename__ = "notifications"
    id          = Column(Integer, primary_key=True)
    project_id  = Column(Integer, ForeignKey("projects.id"), nullable=True)  # null = General Task notification
    user_id     = Column(Integer, ForeignKey("users.id"),    nullable=True)
    type        = Column(String(50))
    message     = Column(Text, nullable=False)
    email_to    = Column(String(200))
    email_sent  = Column(Boolean, default=False)
    read        = Column(Boolean, default=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    project     = relationship("Project", back_populates="notifications")

# ── Audit log ─────────────────────────────────────────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id          = Column(Integer, primary_key=True)
    project_id  = Column(Integer, ForeignKey("projects.id"), nullable=True)
    user_id     = Column(Integer, ForeignKey("users.id"),    nullable=True)
    actor       = Column(String(200))
    entity_type = Column(String(100))
    entity_id   = Column(Integer)
    action      = Column(String(100))
    description = Column(Text)
    old_value   = Column(Text)
    new_value   = Column(Text)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    project     = relationship("Project", back_populates="audit_logs")

# ── Task Assignments ──────────────────────────────────────────────────────────
class TaskAssignment(Base):
    __tablename__ = "task_assignments"
    id           = Column(Integer, primary_key=True, index=True)
    project_id   = Column(Integer, ForeignKey("projects.id"), nullable=True)  # null = General Task (not linked to a project)
    title        = Column(String(300), nullable=False)
    description  = Column(Text)
    assigned_to  = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_by  = Column(Integer, ForeignKey("users.id"), nullable=False)
    team         = Column(String(100))       # Functional Team / Technical Team
    milestone_num= Column(Integer)           # null = "General" (not tied to any Milestone)
    custom_task_id = Column(Integer, ForeignKey("custom_tasks.id"), nullable=True)  # null = "General" (not tied to a specific Task)
    priority     = Column(String(50), default="Medium")   # High / Medium / Low
    status       = Column(String(50), default="Not Started")
    due_date     = Column(DateTime)
    planned_start= Column(DateTime)
    planned_end  = Column(DateTime)
    actual_start = Column(DateTime)
    # actual_end: paired with actual_start so General tasks (no milestone/task
    # link, so nothing to log granular WorkHours entries against) can have
    # their Actual Hours computed directly from a manually-entered actual
    # start/end instead — see _assignment_actual_hours in assignments.py.
    actual_end   = Column(DateTime)
    completed_at = Column(DateTime)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())
    remarks      = Column(Text)
    assignee     = relationship("User", foreign_keys=[assigned_to], backref="assignments_received")
    assigner     = relationship("User", foreign_keys=[assigned_by], backref="assignments_given")

# ── Work Hours Tracking ───────────────────────────────────────────────────────
class WorkHours(Base):
    __tablename__ = "work_hours"
    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    project_id    = Column(Integer, ForeignKey("projects.id"), nullable=True)  # null = hours logged against a General Task
    assignment_id = Column(Integer, ForeignKey("task_assignments.id"), nullable=True)
    task_name     = Column(String(300))
    # Granular level linkage — hours can be logged at Milestone, Task, Subtask
    # or Activity level. "level" records which granularity this entry targets;
    # the matching *_id column is populated, the others stay null.
    level                = Column(String(20))   # Milestone | Task | Subtask | Activity
    custom_milestone_id  = Column(Integer, ForeignKey("custom_milestones.id"), nullable=True)
    custom_task_id       = Column(Integer, ForeignKey("custom_tasks.id"), nullable=True)
    custom_subtask_id    = Column(Integer, ForeignKey("custom_subtasks.id"), nullable=True)
    activity_id          = Column(Integer, ForeignKey("activities.id"), nullable=True)
    date          = Column(Date, nullable=False)
    start_time    = Column(DateTime)
    end_time      = Column(DateTime)
    hours_spent   = Column(Float, default=0.0)
    # Billable / Non-Billable — whether this logged block of hours can be
    # billed to the client. Lives on WorkHours (the single table every hour
    # entry across the app funnels into) so it's available everywhere hours
    # are logged — Work Hours page, Milestone Configuration, Timesheet
    # Calendar's "Log" modal — without duplicating the flag per page.
    is_billable   = Column(Boolean, default=True)
    # work_type supersedes is_billable — stores the full classification of a
    # time entry: Billable, Non-Billable, No Work, Training, R&D.
    # Existing rows are migrated via _run_lightweight_migrations() in main.py.
    work_type     = Column(String(30), nullable=True)
    assigned_hours= Column(Float, default=0.0)
    buffer_hours  = Column(Float, default=0.0)
    buffer_category = Column(String(100))
    notes         = Column(Text)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    user          = relationship("User", backref="work_hours")
    project       = relationship("Project", backref="work_hours")

# ── Custom Milestones per Project ─────────────────────────────────────────────
class CustomMilestone(Base):
    __tablename__ = "custom_milestones"
    id          = Column(Integer, primary_key=True, index=True)
    project_id  = Column(Integer, ForeignKey("projects.id"), nullable=False)
    num         = Column(Integer, nullable=False)
    name        = Column(String(200), nullable=False)
    description = Column(Text)
    responsible = Column(String(100))
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    # Milestone-level Time Management (req: Assignee / Planned Start / Planned
    # End / Actual End / Status, plus start/end time-of-day)
    status        = Column(String(50), default="Not Started")
    assignee      = Column(String(200))
    planned_start = Column(DateTime)
    planned_end   = Column(DateTime)
    actual_start  = Column(DateTime)
    actual_end    = Column(DateTime)
    start_time    = Column(String(10))   # "HH:MM"
    end_time      = Column(String(10))   # "HH:MM"
    # Timeline Report — free-text note explaining any variance between planned
    # and actual end date (or other schedule remarks worth capturing).
    schedule_variance_reason = Column(Text, nullable=True)
    # Milestone Iteration — allows revisiting completed milestones for
    # New Requirements, Major Changes, Bug Fixes, Enhancements, or Other.
    # iteration=1 is the original; each revisit creates a new row with
    # the same num and iteration+1, preserving full history.
    iteration            = Column(Integer, nullable=False, default=1)
    revision_reason      = Column(String(100), nullable=True)
    revision_description = Column(Text, nullable=True)
    tasks       = relationship("CustomTask", back_populates="milestone", cascade="all, delete-orphan")
    reports     = relationship("MilestoneReport", back_populates="milestone", cascade="all, delete-orphan", order_by="MilestoneReport.id")

class CustomTask(Base):
    __tablename__ = "custom_tasks"
    id           = Column(Integer, primary_key=True, index=True)
    milestone_id = Column(Integer, ForeignKey("custom_milestones.id"), nullable=False)
    project_id   = Column(Integer, ForeignKey("projects.id"), nullable=False)
    num          = Column(Integer)
    name         = Column(String(300), nullable=False)
    responsibility = Column(String(100))
    status        = Column(String(50), default="Not Started")
    assignee      = Column(String(200))
    planned_start = Column(DateTime)
    planned_end   = Column(DateTime)
    actual_start  = Column(DateTime)
    actual_end    = Column(DateTime)
    start_time    = Column(String(10))   # "HH:MM"
    end_time      = Column(String(10))   # "HH:MM"
    subtasks     = relationship("CustomSubtask", back_populates="task", cascade="all, delete-orphan")
    milestone    = relationship("CustomMilestone", back_populates="tasks")

class CustomSubtask(Base):
    __tablename__ = "custom_subtasks"
    id        = Column(Integer, primary_key=True, index=True)
    task_id   = Column(Integer, ForeignKey("custom_tasks.id"), nullable=False)
    project_id= Column(Integer, ForeignKey("projects.id"), nullable=False)
    num       = Column(Integer)
    name      = Column(String(300), nullable=False)
    input_type= Column(String(50), default="text")
    response  = Column(Text)
    status    = Column(String(50), default="Not Started")
    assignee      = Column(String(200))
    planned_start = Column(DateTime)
    planned_end   = Column(DateTime)
    actual_start  = Column(DateTime)
    actual_end    = Column(DateTime)
    start_time    = Column(String(10))   # "HH:MM"
    end_time      = Column(String(10))   # "HH:MM"
    # Working Hours Management — entered at Subtask level (and optionally
    # Activity level below); Task/Milestone/Project totals are computed
    # rollups, not stored columns.
    estimated_hours = Column(Float, default=0.0)
    task      = relationship("CustomTask", back_populates="subtasks")
    activities = relationship("Activity", back_populates="subtask", cascade="all, delete-orphan")
    questions  = relationship("SubtaskQuestion", back_populates="subtask", cascade="all, delete-orphan", order_by="SubtaskQuestion.num")
    reports    = relationship("SubtaskReport", back_populates="subtask", cascade="all, delete-orphan", order_by="SubtaskReport.id")

# ── Subtask Questions (optional, multiple per Subtask) ────────────────────────
# Mirrors the standard Milestone system's Question+Response pair (one subtask
# can carry several question/answer rows), but — like CustomSubtask itself —
# keeps the answer inline on the same row instead of a separate Response
# table, since the Custom system has no precedent for a normalized answer
# table and this keeps reads/writes a single round trip.
class SubtaskQuestion(Base):
    __tablename__ = "subtask_questions"
    id            = Column(Integer, primary_key=True, index=True)
    subtask_id    = Column(Integer, ForeignKey("custom_subtasks.id"), nullable=False)
    project_id    = Column(Integer, ForeignKey("projects.id"), nullable=False)
    num           = Column(Integer, default=1)
    question_text = Column(String(500), nullable=False)
    input_type    = Column(String(50), default="text")
    response      = Column(Text)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    subtask       = relationship("CustomSubtask", back_populates="questions")

# ── Activities (optional, nested below a Subtask) ─────────────────────────────
class Activity(Base):
    __tablename__ = "activities"
    id            = Column(Integer, primary_key=True, index=True)
    subtask_id    = Column(Integer, ForeignKey("custom_subtasks.id"), nullable=False)
    project_id    = Column(Integer, ForeignKey("projects.id"), nullable=False)
    name          = Column(String(300), nullable=False)
    status        = Column(String(50), default="Not Started")
    assignee      = Column(String(200))
    planned_start = Column(DateTime)
    planned_end   = Column(DateTime)
    actual_start  = Column(DateTime)
    actual_end    = Column(DateTime)
    start_time    = Column(String(10))   # "HH:MM"
    end_time      = Column(String(10))   # "HH:MM"
    estimated_hours = Column(Float, default=0.0)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    subtask       = relationship("CustomSubtask", back_populates="activities")

# ── Subtask Reports (optional, multiple per Subtask) ──────────────────────────
# Lets several "reports" (each just identified by Report Number / Report Name
# / Department, plus light tracking fields) point at the same Milestone ->
# Task -> Subtask, instead of needing a separate milestone structure built
# per report. report_number is unique within a Subtask to avoid accidental
# duplicates.
class SubtaskReport(Base):
    __tablename__ = "subtask_reports"
    __table_args__ = (UniqueConstraint("subtask_id", "report_number", name="uq_subtask_report_number"),)
    id            = Column(Integer, primary_key=True, index=True)
    subtask_id    = Column(Integer, ForeignKey("custom_subtasks.id"), nullable=False)
    project_id    = Column(Integer, ForeignKey("projects.id"), nullable=False)
    report_number = Column(String(100), nullable=False)
    report_name   = Column(String(300), nullable=False)
    department    = Column(String(150))
    status        = Column(String(50), default="Not Started")
    assigned_to   = Column(String(200))
    due_date      = Column(Date)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())
    subtask       = relationship("CustomSubtask", back_populates="reports")

# ── Milestone Reports (optional, multiple per Milestone) ──────────────────────
# Reports are associated at the Milestone level — several reports can point at
# the same Milestone instead of needing separate task/subtask structure per
# report. report_number is unique within a Milestone to avoid duplicates.
class MilestoneReport(Base):
    __tablename__ = "milestone_reports"
    __table_args__ = (UniqueConstraint("milestone_id", "report_number", name="uq_milestone_report_number"),)
    id            = Column(Integer, primary_key=True, index=True)
    milestone_id  = Column(Integer, ForeignKey("custom_milestones.id"), nullable=False)
    project_id    = Column(Integer, ForeignKey("projects.id"), nullable=False)
    report_number = Column(String(100), nullable=False)
    report_name   = Column(String(300), nullable=False)
    department    = Column(String(150))
    status        = Column(String(50), default="Not Started")
    assigned_to   = Column(String(200))
    due_date      = Column(Date)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())
    milestone     = relationship("CustomMilestone", back_populates="reports")

# ── Timesheet Calendar: Holidays / Leave / Permissions ────────────────────────
class Holiday(Base):
    __tablename__ = "holidays"
    id         = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)  # null = applies to all projects
    date       = Column(Date, nullable=False)
    name       = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    date_from  = Column(Date, nullable=False)
    date_to    = Column(Date, nullable=False)
    leave_type = Column(String(50), default="Casual")   # Casual / Sick / Earned / Unpaid
    status     = Column(String(50), default="Pending")  # Pending / Approved / Rejected
    reason     = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user       = relationship("User", backref="leave_requests")

class Permission(Base):
    """Short permission/short-leave entries (in hours) counted toward the
    minimum 7 working hours/day requirement."""
    __tablename__ = "permissions"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    date       = Column(Date, nullable=False)
    hours      = Column(Float, default=0.0)
    reason     = Column(Text)
    status     = Column(String(50), default="Pending")  # Pending / Approved / Rejected
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user       = relationship("User", backref="permissions")

# ── Billing History ───────────────────────────────────────────────────────────
class ProjectBilling(Base):
    """One billing entry for a project — replaces the single billing_amount field
    on Project. Multiple entries per project allow tracking milestone payments,
    change requests, additional scope, etc. separately. The profitability report
    uses SUM(amount) across all entries as the project's Revenue figure."""
    __tablename__ = "project_billings"
    id           = Column(Integer, primary_key=True, index=True)
    project_id   = Column(Integer, ForeignKey("projects.id"), nullable=False)
    date         = Column(Date, nullable=False)
    amount       = Column(Float, nullable=False, default=0.0)
    billing_type = Column(String(100))   # Milestone Payment / Change Request / etc.
    description  = Column(Text)
    milestone_id = Column(Integer, ForeignKey("custom_milestones.id"), nullable=True)
    remarks      = Column(Text)
    created_by   = Column(Integer, ForeignKey("users.id"))
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    project      = relationship("Project", backref="billings")

# ── Cost Management ───────────────────────────────────────────────────────────
class ProjectCost(Base):
    """One cost entry on a project's Cost tab — Date / Particulars / Category /
    Cost / optional Attachment. Rolled up against Project.budget for the
    Budget vs Actual Cost summary."""
    __tablename__ = "project_costs"
    id           = Column(Integer, primary_key=True, index=True)
    project_id   = Column(Integer, ForeignKey("projects.id"), nullable=False)
    date         = Column(Date, nullable=False)
    particulars  = Column(String(300), nullable=False)
    category     = Column(String(100), nullable=False)
    cost         = Column(Float, nullable=False, default=0.0)
    # Attachment stored on local disk under uploads/costs/<stored_filename>;
    # original_filename is kept separately so downloads show the user's own
    # filename instead of the disk-safe uuid-prefixed one.
    attachment_filename          = Column(String(300))
    attachment_original_filename = Column(String(300))
    created_by   = Column(Integer, ForeignKey("users.id"))
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())
    project      = relationship("Project", back_populates="costs")
    creator      = relationship("User", foreign_keys=[created_by])

# ── Custom Roles ───────────────────────────────────────────────────────────────
class CustomRole(Base):
    """User-defined roles that extend the built-in ALL_ROLES list.
    Created via the (+) button in Team Hub → Add Team Member → Role field."""
    __tablename__ = "custom_roles"
    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# ── Assignment Categories ──────────────────────────────────────────────────────
class AssignmentCategory(Base):
    """Task assignment categories (Business Development / R&D / L&D + custom).
    Created via the (+) button in Task Assignments → Assign Task → Category."""
    __tablename__ = "assignment_categories"
    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# ── Report Templates ──────────────────────────────────────────────────────────
class ReportTemplate(Base):
    """Named, reusable set of report definitions. Apply to any milestone to
    bulk-create independent MilestoneReport copies (copy-on-apply semantics
    — once applied the reports are fully independent from the template)."""
    __tablename__ = "report_templates"
    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(200), nullable=False, unique=True)
    description = Column(Text)
    created_by  = Column(Integer, ForeignKey("users.id"))
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    items       = relationship("ReportTemplateItem", back_populates="template",
                               cascade="all, delete-orphan", order_by="ReportTemplateItem.id")
    creator     = relationship("User", foreign_keys=[created_by])

class ReportTemplateItem(Base):
    """One report definition inside a ReportTemplate."""
    __tablename__ = "report_template_items"
    id            = Column(Integer, primary_key=True, index=True)
    template_id   = Column(Integer, ForeignKey("report_templates.id"), nullable=False)
    report_number = Column(String(100), nullable=False)
    report_name   = Column(String(300), nullable=False)
    department    = Column(String(150))
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    template      = relationship("ReportTemplate", back_populates="items")

# ── Attachments ───────────────────────────────────────────────────────────────
class Attachment(Base):
    """Generic polymorphic attachment — one table handles all entity types.
    entity_type: milestone | task | subtask | activity | report
    Files stored under uploads/attachments/<entity_type>/<stored_filename>."""
    __tablename__ = "attachments"
    id                = Column(Integer, primary_key=True, index=True)
    entity_type       = Column(String(50), nullable=False)
    entity_id         = Column(Integer, nullable=False)
    original_filename = Column(String(300), nullable=False)
    stored_filename   = Column(String(300), nullable=False)
    file_size         = Column(Integer)
    mime_type         = Column(String(100))
    uploaded_by       = Column(Integer, ForeignKey("users.id"))
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    uploader          = relationship("User", foreign_keys=[uploaded_by])
