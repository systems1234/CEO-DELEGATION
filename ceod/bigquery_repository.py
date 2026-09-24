from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from google.cloud import bigquery
from google.oauth2.service_account import Credentials

from ceod.config import Settings
from ceod.exceptions import NotFoundError
from ceod.models import ParsedTaskAssignment, SeedProfile, TaskPriority, TaskRecord, TaskStatus, TeamMember
from ceod.utils import build_random_seed_profiles, generate_row_id, normalise_whatsapp_number

_SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "schema.sql"


def _to_date_str(value: date | None) -> str | None:
  return value.isoformat() if value else None


def _to_timestamp_str(value: datetime | None) -> str | None:
  return value.astimezone(timezone.utc).isoformat() if value else None


class GoogleBigQueryRepository:
  def __init__(self, settings: Settings) -> None:
    self._settings = settings
    self._logger = logging.getLogger(__name__)
    self._project = settings.bigquery_project_id
    self._dataset = settings.bigquery_dataset
    credentials = self._build_credentials(settings)
    self._client = bigquery.Client(project=self._project, credentials=credentials)
    self._schema_ensured = False

  def close(self) -> None:
    self._client.close()

  # -- schema -----------------------------------------------------------

  def ensure_schema(self) -> None:
    if self._schema_ensured:
      return
    self._schema_ensured = True

    ddl = _SCHEMA_SQL_PATH.read_text(encoding="utf-8").format(project=self._project, dataset=self._dataset)
    for statement in filter(None, (s.strip() for s in ddl.split(";"))):
      self._client.query(statement).result()
    self._logger.info("BigQuery schema ensured: %s.%s", self._project, self._dataset)

  # -- team members -------------------------------------------------------

  def get_team_members(self) -> list[TeamMember]:
    rows = self._client.query(
      f"SELECT name, number, email FROM `{self._table('team_members')}` ORDER BY name"
    ).result()
    return [TeamMember(name=row.name, number=row.number, email=row.email or None) for row in rows]

  def get_member_by_name(self, name: str) -> TeamMember | None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", name.strip().lower())]
    )
    rows = list(
      self._client.query(
        f"SELECT name, number, email FROM `{self._table('team_members')}` WHERE LOWER(name) = @name LIMIT 1",
        job_config=job_config,
      ).result()
    )
    return TeamMember(name=rows[0].name, number=rows[0].number, email=rows[0].email or None) if rows else None

  def get_member_by_email(self, email: str) -> TeamMember | None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", email.strip().lower())]
    )
    rows = list(
      self._client.query(
        f"SELECT name, number, email FROM `{self._table('team_members')}` WHERE LOWER(email) = @email LIMIT 1",
        job_config=job_config,
      ).result()
    )
    return TeamMember(name=rows[0].name, number=rows[0].number, email=rows[0].email or None) if rows else None

  def add_team_member(self, name: str, number: str, email: str = "") -> TeamMember:
    clean_name = name.strip()
    clean_number = normalise_whatsapp_number(number)
    clean_email = email.strip().lower() or None

    for member in self.get_team_members():
      if member.name.lower() == clean_name.lower():
        raise ValueError(f'A member named "{clean_name}" already exists')
      if member.number == clean_number:
        raise ValueError(f'Number {clean_number} is already assigned to "{member.name}"')
      if clean_email and member.email == clean_email:
        raise ValueError(f'Email {clean_email} is already assigned to "{member.name}"')

    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("name", "STRING", clean_name),
        bigquery.ScalarQueryParameter("number", "STRING", clean_number),
        bigquery.ScalarQueryParameter("email", "STRING", clean_email),
      ]
    )
    self._client.query(
      f"INSERT INTO `{self._table('team_members')}` (name, number, email, created_at) "
      "VALUES (@name, @number, @email, CURRENT_TIMESTAMP())",
      job_config=job_config,
    ).result()
    self._logger.info("Added team member name=%s number=%s", clean_name, clean_number)
    return TeamMember(name=clean_name, number=clean_number, email=clean_email)

  def seed_random_test_profiles(self, count: int) -> list[SeedProfile]:
    members = self.get_team_members()
    existing_names = {member.name.lower() for member in members}
    existing_numbers = {member.number for member in members}
    profiles = build_random_seed_profiles(count, existing_names, existing_numbers)

    rows = [{"name": p.name, "number": p.number} for p in profiles]
    query_rows = ", ".join(f"(@name{i}, @number{i}, CURRENT_TIMESTAMP())" for i in range(len(rows)))
    if not query_rows:
      return profiles

    params: list[bigquery.ScalarQueryParameter] = []
    for i, row in enumerate(rows):
      params.append(bigquery.ScalarQueryParameter(f"name{i}", "STRING", row["name"]))
      params.append(bigquery.ScalarQueryParameter(f"number{i}", "STRING", row["number"]))

    job_config = bigquery.QueryJobConfig(query_parameters=params)
    self._client.query(
      f"INSERT INTO `{self._table('team_members')}` (name, number, created_at) VALUES {query_rows}",
      job_config=job_config,
    ).result()
    return profiles

  # -- tasks --------------------------------------------------------------

  def create_task(
    self,
    parsed: ParsedTaskAssignment,
    today_iso: str,
    priority: TaskPriority = TaskPriority.MEDIUM,
  ) -> str:
    member = self.get_member_by_name(parsed.assignee)
    if member is None:
      raise NotFoundError(f'Unknown assignee "{parsed.assignee}"')

    row_id = generate_row_id()
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("row_id", "STRING", row_id),
        bigquery.ScalarQueryParameter("assignee_name", "STRING", member.name),
        bigquery.ScalarQueryParameter("assignee_number", "STRING", member.number),
        bigquery.ScalarQueryParameter("task", "STRING", parsed.task),
        bigquery.ScalarQueryParameter("priority", "STRING", priority.value),
        bigquery.ScalarQueryParameter("assign_date", "DATE", today_iso),
        bigquery.ScalarQueryParameter("due_date", "DATE", parsed.due_date),
        bigquery.ScalarQueryParameter("status", "STRING", TaskStatus.PENDING.value),
      ]
    )
    self._client.query(
      f"""
      INSERT INTO `{self._table('tasks')}`
        (row_id, assignee_name, assignee_number, task, priority, assign_date, due_date, status, created_at, updated_at)
      VALUES
        (@row_id, @assignee_name, @assignee_number, @task, @priority, @assign_date, @due_date, @status,
         CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
      """,
      job_config=job_config,
    ).result()
    return row_id

  def get_task_by_row_id(self, row_id: str) -> TaskRecord | None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("row_id", "STRING", row_id)]
    )
    rows = list(
      self._client.query(
        f"SELECT * FROM `{self._table('tasks')}` WHERE row_id = @row_id LIMIT 1",
        job_config=job_config,
      ).result()
    )
    if not rows:
      self._logger.warning("get_task_by_row_id: %s not found", row_id)
      return None
    return self._task_from_row(rows[0])

  def get_latest_pending_task_for_number(self, number: str) -> TaskRecord | None:
    clean_number = normalise_whatsapp_number(number)
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("number", "STRING", clean_number),
        bigquery.ScalarQueryParameter("done", "STRING", TaskStatus.DONE.value),
      ]
    )
    rows = list(
      self._client.query(
        f"""
        SELECT * FROM `{self._table('tasks')}`
        WHERE assignee_number = @number AND status != @done
        ORDER BY created_at DESC
        LIMIT 1
        """,
        job_config=job_config,
      ).result()
    )
    return self._task_from_row(rows[0]) if rows else None

  def list_tasks(self) -> list[TaskRecord]:
    rows = self._client.query(
      f"SELECT * FROM `{self._table('tasks')}` ORDER BY created_at ASC"
    ).result()
    return [self._task_from_row(row) for row in rows]

  def mark_task_done(self, row_id: str, completion_date: str) -> None:
    self._require_task(row_id)
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("row_id", "STRING", row_id),
        bigquery.ScalarQueryParameter("status", "STRING", TaskStatus.DONE.value),
        bigquery.ScalarQueryParameter("completion_date", "DATE", completion_date),
      ]
    )
    self._client.query(
      f"""
      UPDATE `{self._table('tasks')}`
      SET status = @status, completion_date = @completion_date, updated_at = CURRENT_TIMESTAMP()
      WHERE row_id = @row_id
      """,
      job_config=job_config,
    ).result()

  def postpone_task(self, row_id: str, new_date: str, reason: str) -> None:
    self._require_task(row_id)
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("row_id", "STRING", row_id),
        bigquery.ScalarQueryParameter("status", "STRING", TaskStatus.POSTPONED.value),
        bigquery.ScalarQueryParameter("new_date", "DATE", new_date),
        bigquery.ScalarQueryParameter("reason", "STRING", reason),
      ]
    )
    self._client.query(
      f"""
      UPDATE `{self._table('tasks')}`
      SET status = @status, new_date = @new_date, postpone_reason = @reason, updated_at = CURRENT_TIMESTAMP()
      WHERE row_id = @row_id
      """,
      job_config=job_config,
    ).result()

  def commit_task_due_date(self, row_id: str, due_date: str) -> None:
    self._require_task(row_id)
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("row_id", "STRING", row_id),
        bigquery.ScalarQueryParameter("due_date", "DATE", due_date),
      ]
    )
    self._client.query(
      f"""
      UPDATE `{self._table('tasks')}`
      SET due_date = @due_date, updated_at = CURRENT_TIMESTAMP()
      WHERE row_id = @row_id
      """,
      job_config=job_config,
    ).result()

  def get_tasks_due_today(self, today_iso: str) -> list[TaskRecord]:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("today", "DATE", today_iso),
        bigquery.ScalarQueryParameter("pending", "STRING", TaskStatus.PENDING.value),
        bigquery.ScalarQueryParameter("postponed", "STRING", TaskStatus.POSTPONED.value),
      ]
    )
    rows = self._client.query(
      f"""
      SELECT * FROM `{self._table('tasks')}`
      WHERE (status = @pending AND due_date = @today)
         OR (status = @postponed AND new_date = @today)
      ORDER BY created_at ASC
      """,
      job_config=job_config,
    ).result()
    return [self._task_from_row(row) for row in rows]

  def mark_overdue_tasks(self, today_iso: str) -> list[TaskRecord]:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("today", "DATE", today_iso),
        bigquery.ScalarQueryParameter("pending", "STRING", TaskStatus.PENDING.value),
        bigquery.ScalarQueryParameter("postponed", "STRING", TaskStatus.POSTPONED.value),
      ]
    )
    candidates = self._client.query(
      f"""
      SELECT * FROM `{self._table('tasks')}`
      WHERE status IN (@pending, @postponed)
      """,
      job_config=job_config,
    ).result()

    overdue: list[TaskRecord] = []
    for row in candidates:
      task = self._task_from_row(row)
      effective_due = task.effective_due_date
      if effective_due and effective_due < today_iso:
        overdue.append(task)

    if not overdue:
      return []

    row_ids = [task.row_id for task in overdue]
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ArrayQueryParameter("row_ids", "STRING", row_ids),
        bigquery.ScalarQueryParameter("status", "STRING", TaskStatus.OVERDUE.value),
      ]
    )
    self._client.query(
      f"""
      UPDATE `{self._table('tasks')}`
      SET status = @status, updated_at = CURRENT_TIMESTAMP()
      WHERE row_id IN UNNEST(@row_ids)
      """,
      job_config=job_config,
    ).result()

    return [
      TaskRecord(
        row_id=task.row_id,
        assignee_name=task.assignee_name,
        assignee_number=task.assignee_number,
        task=task.task,
        status=TaskStatus.OVERDUE,
        priority=task.priority,
        assign_date=task.assign_date,
        due_date=task.due_date,
        new_date=task.new_date,
        postpone_reason=task.postpone_reason,
      )
      for task in overdue
    ]

  # -- runtime state (pending postpone / commitment flags) -----------------

  def set_pending_postpone(self, number: str, row_id: str) -> None:
    self._upsert_state(self._pending_postpone_key(number), row_id)

  def get_pending_postpone(self, number: str) -> str | None:
    return self._get_state(self._pending_postpone_key(number))

  def clear_pending_postpone(self, number: str) -> None:
    self._delete_state(self._pending_postpone_key(number))

  def set_pending_commitment(self, number: str, row_id: str) -> None:
    self._upsert_state(self._pending_commitment_key(number), row_id)

  def get_pending_commitment(self, number: str) -> str | None:
    return self._get_state(self._pending_commitment_key(number))

  def clear_pending_commitment(self, number: str) -> None:
    self._delete_state(self._pending_commitment_key(number))

  def _upsert_state(self, key: str, value: str) -> None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("key", "STRING", key),
        bigquery.ScalarQueryParameter("value", "STRING", value),
      ]
    )
    self._client.query(
      f"""
      MERGE `{self._table('runtime_state')}` T
      USING (SELECT @key AS key, @value AS value) S
      ON T.key = S.key
      WHEN MATCHED THEN UPDATE SET value = S.value, updated_at = CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN INSERT (key, value, updated_at) VALUES (S.key, S.value, CURRENT_TIMESTAMP())
      """,
      job_config=job_config,
    ).result()

  def _get_state(self, key: str) -> str | None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("key", "STRING", key)]
    )
    rows = list(
      self._client.query(
        f"SELECT value FROM `{self._table('runtime_state')}` WHERE key = @key LIMIT 1",
        job_config=job_config,
      ).result()
    )
    return rows[0].value if rows else None

  def _delete_state(self, key: str) -> None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("key", "STRING", key)]
    )
    self._client.query(
      f"DELETE FROM `{self._table('runtime_state')}` WHERE key = @key",
      job_config=job_config,
    ).result()

  def _pending_postpone_key(self, number: str) -> str:
    return f"POSTPONE_PENDING_{normalise_whatsapp_number(number)}"

  def _pending_commitment_key(self, number: str) -> str:
    return f"COMMITMENT_PENDING_{normalise_whatsapp_number(number)}"

  # -- chat log -------------------------------------------------------------

  def log_message(self, timestamp: str, direction: str, number: str, name: str, text: str) -> None:
    try:
      job_config = bigquery.QueryJobConfig(
        query_parameters=[
          bigquery.ScalarQueryParameter("timestamp", "TIMESTAMP", timestamp),
          bigquery.ScalarQueryParameter("direction", "STRING", direction),
          bigquery.ScalarQueryParameter("number", "STRING", number),
          bigquery.ScalarQueryParameter("name", "STRING", name),
          bigquery.ScalarQueryParameter("message", "STRING", text[:50000]),
        ]
      )
      self._client.query(
        f"""
        INSERT INTO `{self._table('chat_log')}` (timestamp, direction, number, name, message)
        VALUES (@timestamp, @direction, @number, @name, @message)
        """,
        job_config=job_config,
      ).result()
    except Exception as exc:
      self._logger.warning("ChatLog write failed: %s", exc)

  def get_chat_log(self) -> list[dict[str, str]]:
    try:
      rows = self._client.query(
        f"SELECT timestamp, direction, number, name, message FROM `{self._table('chat_log')}` ORDER BY timestamp ASC"
      ).result()
    except Exception:
      return []
    return [
      {
        "timestamp": _to_timestamp_str(row.timestamp) or "",
        "direction": row.direction,
        "number": row.number,
        "name": row.name or "",
        "text": row.message or "",
      }
      for row in rows
    ]

  # -- internals --------------------------------------------------------------

  def _build_credentials(self, settings: Settings) -> Credentials:
    if settings.google_service_account_json:
      info = json.loads(settings.google_service_account_json)
      return Credentials.from_service_account_info(info)
    return Credentials.from_service_account_file(settings.google_application_credentials)

  def _table(self, name: str) -> str:
    return f"{self._project}.{self._dataset}.{name}"

  def _require_task(self, row_id: str) -> None:
    if self.get_task_by_row_id(row_id) is None:
      raise NotFoundError(f"Task row {row_id} not found")

  def _task_status(self, raw: str) -> TaskStatus:
    return TaskStatus(raw or TaskStatus.PENDING.value)

  def _task_priority(self, raw: str | None) -> TaskPriority:
    try:
      return TaskPriority(raw) if raw else TaskPriority.MEDIUM
    except ValueError:
      return TaskPriority.MEDIUM

  def _task_from_row(self, row: bigquery.table.Row) -> TaskRecord:
    return TaskRecord(
      row_id=row.row_id,
      assignee_name=row.assignee_name,
      assignee_number=row.assignee_number,
      task=row.task,
      status=self._task_status(row.status),
      priority=self._task_priority(getattr(row, "priority", None)),
      assign_date=_to_date_str(row.assign_date),
      due_date=_to_date_str(row.due_date),
      new_date=_to_date_str(row.new_date),
      postpone_reason=row.postpone_reason,
      completion_date=_to_date_str(row.completion_date),
    )
