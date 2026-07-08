"""Centralized role permission matrix for User Access Management.

Role hierarchy (highest to lowest):
  Admin           - full access to everything, including Financial Settings.
  Project Manager - elevated access; can create projects; NO Financial Settings.
  FC Lead         - elevated access; CANNOT create projects; NO Financial Settings.
  TC Lead         - elevated access; CANNOT create projects; NO Financial Settings.
  Associate       - standard access (replaces Functional Consultant / Technical Team).
  HR              - team + timesheet management only; no elevated module access.
  Client          - read-only access.

Legacy roles kept for backwards compatibility:
  Functional Consultant, Technical Team — treated same as Associate.

These are independent axes — a role can land in any combination:
  - is_elevated()          -> "all modules" access (assign/delete tasks,
                              manage Cost Management budgets/entries, etc.)
  - can_create_project()   -> allowed to create new projects (Admin + PM only)
  - is_team_manager()      -> create/edit/remove team members & users
  - is_timesheet_manager() -> approve/manage leave, permissions, holidays
                              on behalf of other people

Kept as plain functions (not FastAPI dependencies) so existing routes can
swap conditions with a minimal diff.
"""

ELEVATED_ROLES         = {"Admin", "Project Manager", "FC Lead", "TC Lead"}
PROJECT_CREATOR_ROLES  = {"Admin", "Project Manager"}
TEAM_MANAGER_ROLES     = {"Admin", "HR"}
TIMESHEET_MANAGER_ROLES = {"Admin", "HR"}

# All role strings the app knows about — used by the Team page's role
# dropdown and any place that needs to enumerate valid roles.
ALL_ROLES = [
    "Admin", "Project Manager", "FC Lead", "TC Lead",
    "Associate", "HR", "Client",
]

# Legacy roles kept for backwards compatibility with existing user data
LEGACY_ROLES = ["Functional Consultant", "Technical Team"]

# All roles including legacy — for validation
ALL_VALID_ROLES = ALL_ROLES + LEGACY_ROLES


def is_elevated(user) -> bool:
    """All-module access (Admin / Project Manager / FC Lead / TC Lead).
    Use this wherever the app gates a general module action to Admin-only
    or privileged roles (assign/delete tasks, manage Cost Management, etc.)."""
    return getattr(user, "role", None) in ELEVATED_ROLES


def can_create_project(user) -> bool:
    """Only Admin and Project Manager may create new projects.
    FC Lead / TC Lead are elevated but cannot create projects."""
    return getattr(user, "role", None) in PROJECT_CREATOR_ROLES


def is_team_manager(user) -> bool:
    """Team member / user creation, edit, removal — Admin + HR only."""
    return getattr(user, "role", None) in TEAM_MANAGER_ROLES


def is_timesheet_manager(user) -> bool:
    """Approve/manage leave, permissions, holidays on behalf of others —
    Admin + HR only."""
    return getattr(user, "role", None) in TIMESHEET_MANAGER_ROLES


def is_admin(user) -> bool:
    """Strict Admin-only check. Use for Financial Settings and actions that
    should never be accessible by any other role."""
    return getattr(user, "role", None) == "Admin"
