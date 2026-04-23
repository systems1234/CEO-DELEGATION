from __future__ import annotations

import logging
import re
from datetime import date
from typing import Protocol

from ceod.models import DashboardSnapshot, DashboardStats, IncomingMessage, ParsedPostponeReply, ParsedTaskAssignment, SeedProfile, TaskRecord, TaskStatus, TeamMember
from ceod.utils import extract_row_id, format_date_for_display, is_done_reply, is_postpone_reply, normalise_whatsapp_number, timezone_now, today_iso


class TaskParserProtocol(Protocol):
  def parse_task_assignment(self, raw_message: str, known_names: list[str]) -> ParsedTaskAssignment | None: ...
  def parse_postpone_reply(self, reply_text: str) -> ParsedPostponeReply: ...


class WhatsAppGatewayProtocol(Protocol):
  def send_text(self, to_number: str, body: str) -> str: ...
  def send_file(self, to_number: str, file_bytes: bytes, filename: str, mime_type: str, caption: str = "") -> str: ...


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
  def add_team_member(self, name: str, number: str, sheet_name: str) -> TeamMember: ...


class TaskDelegationService:
  _NAME_PREFIX_TEMPLATE = r"^\s*(?:@[\W_]*)?{name}\b(?:\s+ko\b)?[\s,:-]*"
  _LEADING_POLITENESS_RE = re.compile(r"^(?:please|pls|plz)\b[\s,:-]*", re.IGNORECASE)
  _TRAILING_PUNCTUATION_RE = re.compile(r"^[\s:,\-]+|[\s]+$")

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

    self._logger.info("MSG in from=%s text=%r", message.from_number, message.text[:60])
    self._logger.info("MSG routing: from=%s ceo=%s is_ceo=%s", message.from_number, self._ceo_number, message.from_number == self._ceo_number)
    if message.from_number == self._ceo_number:
      self._logger.info("MSG routed to CEO handler")
      self._handle_ceo_message(message.text)
      return
    self._logger.info("MSG routed to assignee handler")
    self._handle_assignee_reply(message.from_number, message.text)

  def daily_follow_up_check(self) -> None:
    today = today_iso(self._timezone_name)
    due_tasks = self._repository.get_tasks_due_today(today)
    self._logger.info("Daily check: %d due today", len(due_tasks))
    for task in due_tasks:
      self._send_follow_up(task)

    overdue_tasks = self._repository.mark_overdue_tasks(today)
    if overdue_tasks:
      self._logger.info("Marked %d task(s) overdue", len(overdue_tasks))
      self._notify_overdue_tasks(overdue_tasks)

  def get_chat_log(self) -> list[dict[str, str]]:
    return self._repository.get_chat_log()

  def seed_random_test_profiles(self, count: int = 10) -> list[SeedProfile]:
    return self._repository.seed_random_test_profiles(count)

  def add_team_member(self, *, name: str, number: str, sheet_name: str = "") -> TeamMember:
    clean_name = name.strip()
    clean_number = number.strip()
    if not clean_name:
      raise ValueError("Name is required")
    if not clean_number:
      raise ValueError("WhatsApp number is required")
    return self._repository.add_team_member(clean_name, clean_number, sheet_name.strip())

  def send_text_message(self, *, to_number: str, body: str) -> None:
    cleaned_to_number = to_number.strip()
    cleaned_body = body.strip()
    if not cleaned_to_number:
      raise ValueError("Recipient is required")
    if not cleaned_body:
      raise ValueError("Message is required")
    try:
      normalised_to_number = normalise_whatsapp_number(cleaned_to_number)
    except Exception as exc:
      raise ValueError("Recipient number is invalid") from exc
    self._send(normalised_to_number, cleaned_body, name=self._resolve_contact_name(normalised_to_number))

  def send_media_message(
    self,
    *,
    to_number: str,
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    caption: str = "",
  ) -> None:
    cleaned_to_number = to_number.strip()
    cleaned_filename = filename.strip() or "upload"
    cleaned_caption = caption.strip()

    if not cleaned_to_number:
      raise ValueError("Recipient is required")
    if not file_bytes:
      raise ValueError("File is required")
    try:
      normalised_to_number = normalise_whatsapp_number(cleaned_to_number)
    except Exception as exc:
      raise ValueError("Recipient number is invalid") from exc

    self._whatsapp.send_file(normalised_to_number, file_bytes, cleaned_filename, mime_type, cleaned_caption)

    try:
      ts = timezone_now(self._timezone_name).isoformat(timespec="seconds")
      self._repository.log_message(
        ts,
        "out",
        normalised_to_number,
        self._resolve_contact_name(normalised_to_number),
        self._format_media_log_text(cleaned_filename, cleaned_caption),
      )
    except Exception:
      self._logger.warning("Failed to log outgoing media message to %s", normalised_to_number)

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
    task_date = today_iso(self._timezone_name)
    row_id = self._repository.create_task(parsed, task_date)
    self._logger.info("Task created ref=%s assignee=%s", row_id, member.name)
    created_task = TaskRecord(
      row_id=row_id,
      assignee_name=member.name,
      assignee_number=member.number,
      task=parsed.task,
      status=TaskStatus.PENDING,
      assign_date=task_date,
      due_date=parsed.due_date,
    )

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
    normalised_text = self._normalise_task_mentions(text, members)
    parsed = self._parser.parse_task_assignment(normalised_text, known_names)
    if parsed is None:
      parsed = self._parse_explicit_assignment(normalised_text, members)
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
      self._logger.info("Commitment ref=%s due=%s assignee=%s", target_row_id, committed_date, task.assignee_name)
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
      self._logger.info("Postponed ref=%s new_date=%s assignee=%s", target_row_id, parsed.new_date, task.assignee_name)
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
    self._logger.info("Done ref=%s assignee=%s", task.row_id, task.assignee_name)
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
    try:
      self._whatsapp.send_text(to_number, body)
    except Exception as exc:
      self._logger.warning("WhatsApp send to %s failed: %s", to_number, exc)
      return
    try:
      ts = timezone_now(self._timezone_name).isoformat(timespec="seconds")
      self._repository.log_message(ts, "out", to_number, name or to_number, body)
    except Exception:
      self._logger.warning("Failed to log outgoing message to %s", to_number)

  def _normalise_task_mentions(self, text: str, members: list[TeamMember]) -> str:
    normalised_text = text
    for member in sorted(members, key=lambda item: len(item.name), reverse=True):
      for pattern in self._member_mention_patterns(member):
        normalised_text = pattern.sub(member.name, normalised_text)
    return re.sub(r"[ \t]+", " ", normalised_text).strip()

  def _parse_explicit_assignment(self, text: str, members: list[TeamMember]) -> ParsedTaskAssignment | None:
    stripped_text = text.strip()
    if not stripped_text:
      return None

    for member in sorted(members, key=lambda item: len(item.name), reverse=True):
      body = self._strip_member_prefix(stripped_text, member)
      if body is None:
        continue
      task = self._clean_task_body(body)
      if task:
        return ParsedTaskAssignment(
          assignee=member.name,
          task=task,
          due_date=None,
        )
    return None

  def _member_mention_patterns(self, member: TeamMember) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = [
      re.compile(rf"@[\W_]*\+?{re.escape(member.number)}\b", re.IGNORECASE),
    ]
    local_number = member.number[-10:]
    if local_number != member.number:
      patterns.append(re.compile(rf"@[\W_]*\+?{re.escape(local_number)}\b", re.IGNORECASE))
    name_pattern = self._member_name_pattern(member)
    patterns.append(re.compile(rf"@[\W_]*{name_pattern}\b", re.IGNORECASE))
    return patterns

  def _strip_member_prefix(self, text: str, member: TeamMember) -> str | None:
    prefix_re = re.compile(
      self._NAME_PREFIX_TEMPLATE.format(name=self._member_name_pattern(member)),
      re.IGNORECASE,
    )
    match = prefix_re.match(text)
    if match is None:
      return None
    return text[match.end():]

  def _clean_task_body(self, body: str) -> str | None:
    task = self._LEADING_POLITENESS_RE.sub("", body).strip()
    task = self._TRAILING_PUNCTUATION_RE.sub("", task)
    if len(task) < 4 or not any(char.isalnum() for char in task):
      return None
    if task.lower() in {"hi", "hello", "thanks", "thank you", "ok", "okay"}:
      return None
    return task

  def _member_name_pattern(self, member: TeamMember) -> str:
    return r"[\W_]+".join(re.escape(part) for part in member.name.split())

  def _resolve_contact_name(self, number: str) -> str:
    try:
      normalised_number = normalise_whatsapp_number(number)
    except Exception:
      return number
    members = self._repository.get_team_members()
    return next((member.name for member in members if member.number == normalised_number), normalised_number)

  def _format_media_log_text(self, filename: str, caption: str) -> str:
    prefix = f"[File] {filename}"
    if not caption:
      return prefix
    return f"{prefix}\n{caption}"

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
