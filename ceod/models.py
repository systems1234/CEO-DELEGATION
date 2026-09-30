from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskStatus(str, Enum):
  PENDING = "Pending"
  DONE = "Done"
  POSTPONED = "Postponed"
  OVERDUE = "Overdue"


class TaskPriority(str, Enum):
  LOW = "Low"
  MEDIUM = "Medium"
  HIGH = "High"
  CRITICAL = "Critical"


PRIORITY_RANK: dict[TaskPriority, int] = {
  TaskPriority.CRITICAL: 0,
  TaskPriority.HIGH: 1,
  TaskPriority.MEDIUM: 2,
  TaskPriority.LOW: 3,
}


@dataclass(frozen=True)
class TeamMember:
  name: str
  number: str
  email: str | None = None


@dataclass(frozen=True)
class ParsedTaskAssignment:
  assignee: str
  task: str
  due_date: str | None


@dataclass(frozen=True)
class ParsedPostponeReply:
  new_date: str | None
  reason: str | None


@dataclass(frozen=True)
class IncomingMessage:
  from_number: str
  text: str


@dataclass(frozen=True)
class TaskRecord:
  row_id: str
  assignee_name: str
  assignee_number: str
  task: str
  status: TaskStatus
  priority: TaskPriority = TaskPriority.MEDIUM
  assign_date: str | None = None
  due_date: str | None = None
  new_date: str | None = None
  postpone_reason: str | None = None
  completion_date: str | None = None

  @property
  def effective_due_date(self) -> str | None:
    if self.status == TaskStatus.POSTPONED and self.new_date:
      return self.new_date
    return self.due_date


@dataclass(frozen=True)
class SeedProfile:
  name: str
  number: str


@dataclass(frozen=True)
class DashboardStats:
  total_tasks: int
  active_tasks: int
  completed_tasks: int
  overdue_tasks: int
  due_today_tasks: int
  team_members: int


@dataclass(frozen=True)
class DashboardSnapshot:
  generated_at: str
  stats: DashboardStats
  members: list[TeamMember]
  tasks: list[TaskRecord]


@dataclass(frozen=True)
class DateGroup:
  date: str
  tasks: list[TaskRecord]


@dataclass(frozen=True)
class TriageView:
  generated_at: str
  today: str
  overdue_groups: list[DateGroup]
  due_today: list[TaskRecord]


@dataclass(frozen=True)
class MemberKpi:
  name: str
  assigned: int
  completed: int
  overdue: int
  pending: int
  completion_rate: float


@dataclass(frozen=True)
class DailyCompletionPoint:
  date: str
  completed: int


@dataclass(frozen=True)
class KpiReport:
  generated_at: str
  total_tasks: int
  completed_tasks: int
  overdue_tasks: int
  completion_rate: float
  avg_completion_days: float | None
  members: list[MemberKpi]
  daily_trend: list[DailyCompletionPoint]


# -- Department-task delegation architecture (v2) --------------------------


class UserRole(str, Enum):
  ADMIN = "Admin"
  MANAGER = "Manager"
  TL = "TL"
  SENIOR = "Senior"
  MEMBER = "Member"


# Senior is treated the same as TL for now (per product decision); both get
# the management view (assign to doers, revise dates, see Department_Tasks).
MANAGEMENT_ROLES = frozenset({UserRole.ADMIN, UserRole.MANAGER, UserRole.TL, UserRole.SENIOR})

DOER_TASK_STATUS_COMPLETED = "Completed"

DOER_UPDATE_STATUSES = ("In Progress", "Abandon")


@dataclass(frozen=True)
class EmployeeRecord:
  work_email: str
  full_name: str
  employee_number: str | None
  department: str | None
  job_title: str | None


@dataclass(frozen=True)
class UserAccount:
  user_id: str
  name: str
  role: UserRole
  active: bool


@dataclass(frozen=True)
class LoginResult:
  email: str
  name: str
  role: UserRole
  department: str | None

  @property
  def is_management(self) -> bool:
    return self.role in MANAGEMENT_ROLES


@dataclass(frozen=True)
class AllTask:
  task_id: str
  created_date_time: str
  task_brief: str
  assigned_department: str
  ceo_task_due_date: str | None
  priority: TaskPriority
  ny_task_id: str | None


@dataclass(frozen=True)
class DepartmentTaskAssignment:
  task_id: str
  task_brief: str
  comments: str | None
  department_due_date: str | None
  priority: TaskPriority
  assigned_by: str
  assigned_to_doer: str
  doer_task_id: str
  row_created_at: str


@dataclass(frozen=True)
class TaskRevision:
  task_id: str
  revised_id: str
  revised_date: str
  revised_by: str
  revised_at: str


@dataclass(frozen=True)
class DoerTaskCompletion:
  task_id: str
  task_brief: str
  task_status: str
  completion_date: str | None
  comments: str | None
  assigned_by: str
  doer_task_id: str
  submitted_by: str


@dataclass(frozen=True)
class DoerTaskUpdateEntry:
  task_id: str
  task_update_id: str
  doer_task_id: str
  task_update: str
  comments: str | None
  submitted_by: str
  row_created_at: str


@dataclass(frozen=True)
class ReportingRelation:
  tl_name: str | None
  manager_name: str | None
  emp_name: str
  emp_id: str
  tl_email: str | None = None
  manager_email: str | None = None


@dataclass(frozen=True)
class DepartmentQueueItem:
  """One All_Tasks row plus its current (latest, if any) Department_Tasks assignment."""

  task: AllTask
  current_assignment: DepartmentTaskAssignment | None
  revisions: list[TaskRevision]
  completion: DoerTaskCompletion | None
  latest_update: DoerTaskUpdateEntry | None


@dataclass(frozen=True)
class DoerTaskView:
  """What a doer sees for one active assignment: the department task plus its live status."""

  assignment: DepartmentTaskAssignment
  all_task: AllTask
  latest_update: DoerTaskUpdateEntry | None
  completion: DoerTaskCompletion | None
  revisions: list[TaskRevision]

  @property
  def effective_due_date(self) -> str | None:
    if self.revisions:
      return max(self.revisions, key=lambda r: r.revised_at).revised_date
    return self.assignment.department_due_date

  @property
  def is_completed(self) -> bool:
    return self.completion is not None
