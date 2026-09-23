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
