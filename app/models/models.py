from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base
import enum

class UserRole(str, enum.Enum):
    admin = "Admin"
    functional_consultant = "Functional Consultant"
    technical_team = "Technical Team"
    client = "Client"

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

# ── Users ────────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(120), nullable=False)
    email         = Column(String(200), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    role          = Column(String(50), default=UserRole.functional_consultant)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    projects      = relationship("ProjectMember", back_populates="user")

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
    functional_consultant  = Column(String(200))
    technical_lead         = Column(String(200))
    description            = Column(Text)
    start_date             = Column(DateTime)
    end_date               = Column(DateTime)
    status                 = Column(String(50), default="Not Started")
    progress               = Column(Float, default=0.0)
    created_by             = Column(Integer, ForeignKey("users.id"))
    created_at             = Column(DateTime(timezone=True), server_default=func.now())
    updated_at             = Column(DateTime(timezone=True), onupdate=func.now())
    milestones             = relationship("ProjectMilestone", back_populates="project", cascade="all, delete-orphan")
    members                = relationship("ProjectMember",    back_populates="project", cascade="all, delete-orphan")
    notifications          = relationship("Notification",     back_populates="project", cascade="all, delete-orphan")
    audit_logs             = relationship("AuditLog",         back_populates="project", cascade="all, delete-orphan")

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
    project_id  = Column(Integer, ForeignKey("projects.id"), nullable=False)
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
    project_id   = Column(Integer, ForeignKey("projects.id"), nullable=False)
    title        = Column(String(300), nullable=False)
    description  = Column(Text)
    assigned_to  = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_by  = Column(Integer, ForeignKey("users.id"), nullable=False)
    team         = Column(String(100))       # Functional Team / Technical Team
    milestone_num= Column(Integer)
    priority     = Column(String(50), default="Medium")   # High / Medium / Low
    status       = Column(String(50), default="Not Started")
    due_date     = Column(DateTime)
    completed_at = Column(DateTime)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())
    remarks      = Column(Text)
    assignee     = relationship("User", foreign_keys=[assigned_to], backref="assignments_received")
    assigner     = relationship("User", foreign_keys=[assigned_by], backref="assignments_given")
