from __future__ import annotations

import logging
from datetime import date
from typing import Protocol

from ceod.models import DashboardSnapshot, DashboardStats, IncomingMessage, ParsedPostponeReply, ParsedTaskAssignment, SeedProfile, TaskRecord, TaskStatus, TeamMember
from ceod.utils import extract_row_id, format_date_for_display, is_done_reply, is_postpone_reply, timezone_now, today_iso


class TaskParserProtocol(Protocol):
  def parse_task_assignment(self, raw_message: str, known_names: list[str]) -> ParsedTaskAssignment | None: ...
  def parse_postpone_reply(self, reply_text: str) -> ParsedPostponeReply: ...


class WhatsAppGatewayProtocol(Protocol):
  def send_text(self, to_number: str, body: str) -> str: ...


class TaskRepositoryProtocol(Protocol):
  def get_team_members(self) -> list[TeamMember]: ...
  def get_member_by_name(self, name: str) -> TeamMember | None: ...
  def create_task(self, parsed: ParsedTaskAssignment, today_iso: str) -> str: ...
  def get_task_by_row_id(self, row_id: str) -> TaskRecord | None: ...
  def get_latest_pending_task_for_number(self, number: str) -> TaskRecord | None: ...
  def list_tasks(self) -> list[TaskRecord]: ...
  def mark_task_done(self, row_id: str, completion_date: str) -> None: ...
  def postpone_task(self, row_id: str, new_date: str, reason: str) -> None: ...
  def get_tasks_due_today(self, today_iso: str) -> list[TaskRecord]: ...
  def mark_overdue_tasks(self, today_iso: str) -> list[TaskRecord]: ...
  def set_pending_postpone(self, number: str, row_id: str) -> None: ...
  def get_pending_postpone(self, number: str) -> str | None: ...
  def clear_pending_postpone(self, number: str) -> None: ...
  def set_pending_commitment(self, number: str, row_id: str) -> None: ...
  def get_pending_commitment(self, number: str) -> str | None: ...
  def clear_pending_commitment(self, number: str) -> None: ...
  def commit_task_due_date(self, row_id: str, due_date: str) -> None: ...
  def log_message(self, timestamp: str, direction: str, number: str, name: str, text: str) -> None: ...
  def get_chat_log(self) -> list[dict[str, str]]: ...
  def seed_random_test_profiles(self, count: int) -> list[SeedProfile]: ...


class TaskDelegationService:
  def __init__(
    self,
    *,
    parser: TaskParserProtocol,
    whatsapp: WhatsAppGatewayProtocol,
    repository: TaskRepositoryProtocol,
    ceo_number: str,
    timezone_name: str,
  ) -> None:
    self._parser = parser
    self._whatsapp = whatsapp
    self._repository = repository
    self._ceo_number = ceo_number
    self._timezone_name = timezone_name
    self._logger = logging.getLogger(__name__)

  def handle_incoming_message(self, message: IncomingMessage) -> None:
    try:
      members = self._repository.get_team_members()
      name = next((m.name for m in members if m.number == message.from_number), message.from_number)
      ts = timezone_now(self._timezone_name).isoformat(timespec="seconds")
      self._repository.log_message(ts, "in", message.from_number, name, message.text)
    except Exception:
      self._logger.warning("Failed to log incoming message from %s", message.from_number)

    if message.from_number == self._ceo_number:
      self._handle_ceo_message(message.text)
      return
    self._handle_assignee_reply(message.from_number, message.text)

  def daily_follow_up_check(self) -> None:
    today = today_iso(self._timezone_name)
    due_tasks = self._repository.get_tasks_due_today(today)
    for task in due_tasks:
      self._send_follow_up(task)

    overdue_tasks = self._repository.mark_overdue_tasks(today)
    if overdue_tasks:
      self._notify_overdue_tasks(overdue_tasks)

  def get_chat_log(self) -> list[dict[str, str]]:
    return self._repository.get_chat_log()

  def seed_random_test_profiles(self, count: int = 10) -> list[SeedProfile]:
    return self._repository.seed_random_test_profiles(count)

  def assign_task(
    self,
    *,
    assignee: str,
    task: str,
    due_date: str | None = None,
    notify_ceo: bool = False,
    notify_assignee: bool = True,
  ) -> TaskRecord:
    cleaned_assignee = assignee.strip()
    cleaned_task = task.strip()
    cleaned_due_date = due_date.strip() if due_date else None

    if not cleaned_assignee:
      raise ValueError("Assignee is required")
    if not cleaned_task:
      raise ValueError("Task description is required")
    if cleaned_due_date:
      try:
        date.fromisoformat(cleaned_due_date)
      except ValueError as exc:
        raise ValueError("Due date must be in YYYY-MM-DD format") from exc

    member = self._repository.get_member_by_name(cleaned_assignee)
    if member is None:
      raise ValueError(f'Unknown assignee "{cleaned_assignee}"')

    parsed = ParsedTaskAssignment(
      assignee=member.name,
      task=cleaned_task,
      due_date=cleaned_due_date or None,
    )
    row_id = self._repository.create_task(parsed, today_iso(self._timezone_name))
    created_task = self._repository.get_task_by_row_id(row_id)
    if created_task is None:
      raise RuntimeError(f"Created task {row_id} could not be reloaded")

    if notify_ceo:
      due_text = f"Due: {format_date_for_display(parsed.due_date)}" if parsed.due_date else "No due date set"
      self._notify_ceo(
        f"Task assigned.\n\nAssignee: {member.name}\nTask: {parsed.task}\n{due_text}\nRef: {row_id}"
      )

    if notify_assignee:
      self._send(
        member.number,
        (
          f"Hi {member.name}, you have been assigned a new task:\n\n"
          f"*{parsed.task}*\n\n"
          "By when will you complete this? Please reply with a date (DD/MM/YYYY).\n\n"
          f"Ref: {row_id}"
        ),
        name=member.name,
      )
      self._repository.set_pending_commitment(member.number, row_id)

    return created_task

  def get_dashboard_snapshot(self) -> DashboardSnapshot:
    generated_at = timezone_now(self._timezone_name).isoformat(timespec="seconds")
    today = today_iso(self._timezone_name)
    members = self._repository.get_team_members()
    tasks = list(reversed(self._repository.list_tasks()))
    due_today_tasks = self._repository.get_tasks_due_today(today)

    stats = DashboardStats(
      total_tasks=len(tasks),
      active_tasks=sum(1 for task in tasks if task.status in {TaskStatus.PENDING, TaskStatus.POSTPONED}),
      completed_tasks=sum(1 for task in tasks if task.status == TaskStatus.DONE),
      overdue_tasks=sum(1 for task in tasks if task.status == TaskStatus.OVERDUE),
      due_today_tasks=len(due_today_tasks),
      team_members=len(members),
    )
    return DashboardSnapshot(
      generated_at=generated_at,
      stats=stats,
      members=members,
      tasks=tasks,
    )

  def _handle_ceo_message(self, text: str) -> None:
    if not text.strip():
      return

    members = self._repository.get_team_members()
    known_names = [member.name for member in members]
    parsed = self._parser.parse_task_assignment(text, known_names)
    if parsed is None:
      self._logger.info("Ignoring non-task CEO message")
      return

    try:
      self.assign_task(
        assignee=parsed.assignee,
        task=parsed.task,
        due_date=parsed.due_date,
        notify_ceo=True,
        notify_assignee=True,
      )
    except ValueError:
      self._send(
        self._ceo_number,
        (
          f'Could not find team member "{parsed.assignee}" in the Config sheet.\n'
          "Please add them first or check the spelling."
        ),
        name="CEO",
      )

  def _handle_assignee_reply(self, from_number: str, text: str) -> None:
    if not text.strip():
      return

    lower = text.lower()
    row_id = extract_row_id(text)

    pending_commitment = self._repository.get_pending_commitment(from_number)
    if pending_commitment:
      from ceod.utils import parse_date_flexible
      target_row_id = row_id or pending_commitment
      committed_date = parse_date_flexible(text.strip())
      if committed_date is None:
        self._send(from_number, "Please reply with a valid date in DD/MM/YYYY format.\nExample: 25/04/2026")
        return
      task = self._repository.get_task_by_row_id(target_row_id)
      if task is None:
        self._send(from_number, f"Task ref {target_row_id} not found.")
        return
      self._repository.commit_task_due_date(target_row_id, committed_date)
      self._repository.clear_pending_commitment(from_number)
      self._send(
        from_number,
        f"Got it! I'll follow up on {format_date_for_display(committed_date)}.\n\nRef: {target_row_id}",
        name=task.assignee_name,
      )
      self._notify_ceo(
        f"Commitment Received\n\nAssignee: {task.assignee_name}\nTask: {task.task}\nCommitted date: {format_date_for_display(committed_date)}\nRef: {target_row_id}"
      )
      return

    if is_done_reply(lower):
      task = self._repository.get_task_by_row_id(row_id) if row_id else self._repository.get_latest_pending_task_for_number(from_number)
      if task is None:
        self._send(from_number, "Could not find your task. Please include the Reference ID in your reply.")
        return
      self._process_done_reply(from_number, task)
      return

    pending_postpone = self._repository.get_pending_postpone(from_number)
    if pending_postpone:
      parsed = self._parser.parse_postpone_reply(text)
      target_row_id = row_id or pending_postpone
      if not parsed.new_date or not parsed.reason:
        self._send(from_number, "Could not parse your reply. Please use:\n*NEW DATE: DD/MM/YYYY | REASON: <your reason>*")
        return

      task = self._repository.get_task_by_row_id(target_row_id)
      if task is None:
        self._send(from_number, f"Task ref {target_row_id} not found.")
        return

      self._repository.postpone_task(target_row_id, parsed.new_date, parsed.reason)
      self._repository.clear_pending_postpone(from_number)
      self._send(
        from_number,
        f"Got it. Task rescheduled to {format_date_for_display(parsed.new_date)}.",
        name=task.assignee_name,
      )
      self._notify_ceo(
        (
          "Task Postponed\n\n"
          f"Assignee: {task.assignee_name}\n"
          f"Task: {task.task}\n"
          f"New date: {format_date_for_display(parsed.new_date)}\n"
          f"Reason: {parsed.reason}\n"
          f"Ref: {target_row_id}"
        )
      )
      return

    if is_postpone_reply(lower):
      task = self._repository.get_task_by_row_id(row_id) if row_id else self._repository.get_latest_pending_task_for_number(from_number)
      if task is None:
        self._send(from_number, "Could not find your task. Please include the Reference ID.")
        return
      self._repository.set_pending_postpone(from_number, task.row_id)
      self._send_postpone_prompt(task)
      return

    self._logger.info("Ignoring unrecognised assignee reply from=%s", from_number)

  def _process_done_reply(self, from_number: str, task: TaskRecord) -> None:
    completion_date = today_iso(self._timezone_name)
    self._repository.mark_task_done(task.row_id, completion_date)
    self._send(from_number, "Task marked as Done. Great work.", name=task.assignee_name)
    self._notify_ceo(
      (
        "Task Completed\n\n"
        f"Assignee: {task.assignee_name}\n"
        f"Task: {task.task}\n"
        f"Completed: {format_date_for_display(completion_date)}\n"
        f"Ref: {task.row_id}"
      )
    )

  def _send(self, to_number: str, body: str, name: str = "") -> None:
    self._whatsapp.send_text(to_number, body)
    try:
      ts = timezone_now(self._timezone_name).isoformat(timespec="seconds")
      self._repository.log_message(ts, "out", to_number, name or to_number, body)
    except Exception:
      self._logger.warning("Failed to log outgoing message to %s", to_number)

  def _notify_ceo(self, message: str) -> None:
    self._send(self._ceo_number, f"Task Update\n\n{message}", name="CEO")

  def _send_follow_up(self, task: TaskRecord) -> None:
    self._send(
      task.assignee_number,
      (
        f"Hi {task.assignee_name}\n\n"
        f"Your task is due today:\n*{task.task}*\n\n"
        "Please reply:\n"
        "*Done* - if completed\n"
        "*Postpone* - if you need more time\n\n"
        f"(Reference: {task.row_id})"
      ),
      name=task.assignee_name,
    )

  def _send_postpone_prompt(self, task: TaskRecord) -> None:
    self._send(
      task.assignee_number,
      (
        f"Hi {task.assignee_name}, understood.\n\n"
        f"For task: *{task.task}*\n\n"
        "Please reply in this format:\n"
        "*NEW DATE: DD/MM/YYYY | REASON: <your reason>*\n\n"
        f"(Reference: {task.row_id})"
      ),
      name=task.assignee_name,
    )

  def _notify_overdue_tasks(self, overdue_tasks: list[TaskRecord]) -> None:
    lines = [
      (
        f"{index + 1}. {task.assignee_name} - {task.task}\n"
        f"   Was due: {format_date_for_display(task.effective_due_date)} | Ref: {task.row_id}"
      )
      for index, task in enumerate(overdue_tasks)
    ]
    self._notify_ceo(f"Overdue Tasks ({len(overdue_tasks)})\n\n" + "\n\n".join(lines))

    for task in overdue_tasks:
      self._send(
        task.assignee_number,
        (
          f"Hi {task.assignee_name}, your task is overdue.\n\n"
          f"Task: {task.task}\n"
          f"Was due: {format_date_for_display(task.effective_due_date)}\n\n"
          "Please reply:\n"
          "*Done* - if completed\n"
          "*Postpone* - if you need more time\n\n"
          f"(Reference: {task.row_id})"
        ),
        name=task.assignee_name,
      )
