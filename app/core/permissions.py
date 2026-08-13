"""Centralized role permission matrix for User Access Management.

Role hierarchy (highest to lowest):
  Admin                  - full access to everything, including Financial Settings.
  Project Manager        - elevated access; can create projects; NO Financial Settings.
  FC Lead                - elevated access; CANNOT create projects; NO Financial Settings.
  TC Lead                - elevated access; CANNOT create projects; NO Financial Settings.
  Associate Data Analyst - all-module VIEW access + Cost Management write + Financial
                           Settings; CANNOT assign or delete tasks.
  Associate              - standard access (replaces Functional Consultant / Technical Team).
  HR                     - team + timesheet management only; no elevated module access.
  Client                 - read-only access.

Legacy roles kept for backwards compatibility:
  Functional Consultant, Technical Team — treated same as Associate.

Permission axes — a role can land in any combination:
  - is_elevated()          -> full "elevated" access: assign/delete tasks AND
                              all-module visibility (Admin / PM / FC Lead / TC Lead only).
  - can_view_elevated()    -> see all users' data, audit log, work hours etc.
                              = is_elevated() PLUS Associate Data Analyst.
  - can_manage_cost()      -> add/edit/delete Cost Management entries
                              = is_elevated() PLUS Associate Data Analyst.
  - can_create_project()   -> create new projects (Admin + PM only).
  - is_team_manager()      -> create/edit/remove team members & users.
  - is_timesheet_manager() -> approve/manage leave, permissions, holidays.

Kept as plain functions (not FastAPI dependencies) so existing routes can
swap conditions with a minimal diff.
"""

ELEVATED_ROLES         = {"Admin", "Project Manager", "FC Lead", "TC Lead"}
DA_ROLES               = {"Associate Data Analyst"}
PROJECT_CREATOR_ROLES  = {"Admin", "Project Manager", "FC Lead"}
TEAM_MANAGER_ROLES     = {"Admin", "HR", "Project Manager"}
TIMESHEET_MANAGER_ROLES = {"Admin", "HR", "Project Manager"}
FINANCIAL_SETTINGS_ROLES = {"Admin", "HR", "Project Manager", "Associate Data Analyst"}

# All role strings the app knows about — used by the Team page's role
# dropdown and any place that needs to enumerate valid roles.
ALL_ROLES = [
    "Admin", "Project Manager", "FC Lead", "TC Lead",
    "Associate Data Analyst", "Associate", "HR", "Client",
]

# Legacy roles kept for backwards compatibility with existing user data
LEGACY_ROLES = ["Functional Consultant", "Technical Team"]

# All roles including legacy — for validation
ALL_VALID_ROLES = ALL_ROLES + LEGACY_ROLES


def is_elevated(user) -> bool:
    """Full elevated access: assign/delete tasks + all-module visibility.
    Admin / Project Manager / FC Lead / TC Lead ONLY.
    Associate Data Analyst is intentionally excluded — use can_view_elevated()
    or can_manage_cost() for checks that DA should pass."""
    return getattr(user, "role", None) in ELEVATED_ROLES


def is_data_analyst(user) -> bool:
    """Associate Data Analyst role check."""
    return getattr(user, "role", None) in DA_ROLES


def can_view_elevated(user) -> bool:
    """See all users' data (work hours, assignments, audit log, reports).
    Elevated roles + Associate Data Analyst."""
    return getattr(user, "role", None) in ELEVATED_ROLES | DA_ROLES


def can_manage_cost(user) -> bool:
    """Add / edit / delete Cost Management entries and Financial Settings.
    Elevated roles + Associate Data Analyst."""
    return getattr(user, "role", None) in ELEVATED_ROLES | DA_ROLES


def can_create_project(user) -> bool:
    """Admin, Project Manager, and FC Lead may create new projects."""
    return getattr(user, "role", None) in PROJECT_CREATOR_ROLES


def is_team_manager(user) -> bool:
    """Team member / user creation, edit, removal — Admin + HR + PM."""
    return getattr(user, "role", None) in TEAM_MANAGER_ROLES


def is_timesheet_manager(user) -> bool:
    """Approve/manage leave, permissions, holidays on behalf of others."""
    return getattr(user, "role", None) in TIMESHEET_MANAGER_ROLES


def is_admin(user) -> bool:
    """Strict Admin-only check."""
    return getattr(user, "role", None) == "Admin"


def can_access_financial_settings(user) -> bool:
    """Financial Settings access — Admin, HR, Project Manager, Associate Data Analyst."""
    return getattr(user, "role", None) in FINANCIAL_SETTINGS_ROLES
