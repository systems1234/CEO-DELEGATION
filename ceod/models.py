from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskStatus(str, Enum):
  PENDING = "Pending"
  DONE = "Done"
  POSTPONED = "Postponed"
  OVERDUE = "Overdue"


@dataclass(frozen=True)
class TeamMember:
  name: str
  number: str
  sheet_name: str


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
  sheet_name: str


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
