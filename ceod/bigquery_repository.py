from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

from google.cloud import bigquery
from google.oauth2.service_account import Credentials

from ceod.config import Settings
from ceod.exceptions import NotFoundError
from ceod.models import (
  AllTask,
  DepartmentTaskAssignment,
  DoerTaskCompletion,
  DoerTaskUpdateEntry,
  DOER_TASK_STATUS_COMPLETED,
  EmployeeRecord,
  ParsedTaskAssignment,
  ReportingRelation,
  SeedProfile,
  TaskPriority,
  TaskRecord,
  TaskRevision,
  TaskStatus,
  TeamMember,
  UserAccount,
  UserRole,
)
from ceod.utils import build_random_seed_profiles, generate_prefixed_id, generate_row_id, normalise_whatsapp_number

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

  # -- HR employee lookup (cross-dataset, read-only) -----------------------

  def get_employee_by_email(self, email: str) -> EmployeeRecord | None:
    """Returns the employee record only if Project_id grants access to this app."""
    pattern = rf"(^|,)\s*{re.escape(self._settings.sso_project_tag)}\s*(,|$)"
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("email", "STRING", email.strip().lower()),
        bigquery.ScalarQueryParameter("pattern", "STRING", pattern),
      ]
    )
    rows = list(
      self._client.query(
        f"""
        SELECT work_email, full_name, employee_number, department, job_title
        FROM `{self._hr_table()}`
        WHERE LOWER(work_email) = @email
          AND Project_id IS NOT NULL
          AND REGEXP_CONTAINS(Project_id, @pattern)
        LIMIT 1
        """,
        job_config=job_config,
      ).result()
    )
    if not rows:
      return None
    row = rows[0]
    return EmployeeRecord(
      work_email=row.work_email,
      full_name=row.full_name,
      employee_number=row.employee_number,
      department=row.department,
      job_title=row.job_title,
    )

  # -- Users (roles) --------------------------------------------------------

  def get_user(self, email: str) -> UserAccount | None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("user_id", "STRING", email.strip().lower())]
    )
    rows = list(
      self._client.query(
        f"SELECT user_id, name, role, active FROM `{self._table('Users')}` WHERE LOWER(user_id) = @user_id LIMIT 1",
        job_config=job_config,
      ).result()
    )
    return self._user_from_row(rows[0]) if rows else None

  def list_users(self) -> list[UserAccount]:
    rows = self._client.query(
      f"SELECT user_id, name, role, active FROM `{self._table('Users')}` ORDER BY name"
    ).result()
    return [self._user_from_row(row) for row in rows]

  def upsert_user(self, *, user_id: str, name: str, role: UserRole, active: bool = True) -> UserAccount:
    clean_user_id = user_id.strip().lower()
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("user_id", "STRING", clean_user_id),
        bigquery.ScalarQueryParameter("name", "STRING", name.strip()),
        bigquery.ScalarQueryParameter("role", "STRING", role.value),
        bigquery.ScalarQueryParameter("active", "BOOL", active),
      ]
    )
    self._client.query(
      f"""
      MERGE `{self._table('Users')}` T
      USING (SELECT @user_id AS user_id, @name AS name, @role AS role, @active AS active) S
      ON T.user_id = S.user_id
      WHEN MATCHED THEN UPDATE SET name = S.name, role = S.role, active = S.active
      WHEN NOT MATCHED THEN INSERT (user_id, name, role, active, created_at)
        VALUES (S.user_id, S.name, S.role, S.active, CURRENT_TIMESTAMP())
      """,
      job_config=job_config,
    ).result()
    return UserAccount(user_id=clean_user_id, name=name.strip(), role=role, active=active)

  def deactivate_user(self, email: str) -> None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("user_id", "STRING", email.strip().lower())]
    )
    self._client.query(
      f"UPDATE `{self._table('Users')}` SET active = FALSE WHERE user_id = @user_id",
      job_config=job_config,
    ).result()

  # -- Reporting_to ----------------------------------------------------------

  def list_reporting_relations(self) -> list[ReportingRelation]:
    rows = self._client.query(
      f"""
      SELECT TL_Name, Manager_Name, Emp_Name, Emp_id, TL_Email, Manager_Email
      FROM `{self._table('Reporting_to')}` ORDER BY Emp_Name
      """
    ).result()
    return [self._reporting_relation_from_row(row) for row in rows]

  def get_team_for(self, email: str) -> list[ReportingRelation]:
    """Doers reporting to this TL or Manager (matched by the login's email)."""
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", email.strip().lower())]
    )
    rows = list(
      self._client.query(
        f"""
        SELECT TL_Name, Manager_Name, Emp_Name, Emp_id, TL_Email, Manager_Email
        FROM `{self._table('Reporting_to')}`
        WHERE LOWER(TL_Email) = @email OR LOWER(Manager_Email) = @email
        ORDER BY Emp_Name
        """,
        job_config=job_config,
      ).result()
    )
    return [self._reporting_relation_from_row(row) for row in rows]

  def add_reporting_relation(
    self,
    *,
    tl_name: str | None,
    manager_name: str | None,
    emp_name: str,
    emp_id: str,
    tl_email: str | None = None,
    manager_email: str | None = None,
  ) -> ReportingRelation:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("tl_name", "STRING", tl_name.strip() if tl_name else None),
        bigquery.ScalarQueryParameter("manager_name", "STRING", manager_name.strip() if manager_name else None),
        bigquery.ScalarQueryParameter("emp_name", "STRING", emp_name.strip()),
        bigquery.ScalarQueryParameter("emp_id", "STRING", emp_id.strip()),
        bigquery.ScalarQueryParameter("tl_email", "STRING", tl_email.strip().lower() if tl_email else None),
        bigquery.ScalarQueryParameter("manager_email", "STRING", manager_email.strip().lower() if manager_email else None),
      ]
    )
    self._client.query(
      f"""
      INSERT INTO `{self._table('Reporting_to')}` (TL_Name, Manager_Name, Emp_Name, Emp_id, TL_Email, Manager_Email)
      VALUES (@tl_name, @manager_name, @emp_name, @emp_id, @tl_email, @manager_email)
      """,
      job_config=job_config,
    ).result()
    return ReportingRelation(
      tl_name=tl_name,
      manager_name=manager_name,
      emp_name=emp_name,
      emp_id=emp_id,
      tl_email=tl_email.strip().lower() if tl_email else None,
      manager_email=manager_email.strip().lower() if manager_email else None,
    )

  # -- All_Tasks (LLM-posted, department-level) -------------------------------

  def list_all_tasks_for_department(self, department: str) -> list[AllTask]:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("department", "STRING", department)]
    )
    rows = self._client.query(
      f"""
      SELECT Task_id, Created_date_time, Task_brief, Assigned_Department, CEO_Task_due_date, Priority
      FROM `{self._table('All_Tasks')}`
      WHERE Assigned_Department = @department
      ORDER BY Created_date_time DESC
      """,
      job_config=job_config,
    ).result()
    return [self._all_task_from_row(row) for row in rows]

  def list_all_tasks(self) -> list[AllTask]:
    rows = self._client.query(
      f"""
      SELECT Task_id, Created_date_time, Task_brief, Assigned_Department, CEO_Task_due_date, Priority
      FROM `{self._table('All_Tasks')}`
      ORDER BY Created_date_time DESC
      """
    ).result()
    return [self._all_task_from_row(row) for row in rows]

  def get_all_task(self, task_id: str) -> AllTask | None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("task_id", "STRING", task_id)]
    )
    rows = list(
      self._client.query(
        f"""
        SELECT Task_id, Created_date_time, Task_brief, Assigned_Department, CEO_Task_due_date, Priority
        FROM `{self._table('All_Tasks')}` WHERE Task_id = @task_id LIMIT 1
        """,
        job_config=job_config,
      ).result()
    )
    return self._all_task_from_row(rows[0]) if rows else None

  # -- Department_Tasks (current assignment = latest row per Task_id) --------

  def get_current_department_assignment(self, task_id: str) -> DepartmentTaskAssignment | None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("task_id", "STRING", task_id)]
    )
    rows = list(
      self._client.query(
        f"""
        SELECT Task_id, Task_brief, comments, Department_due_date, Priority, Assigned_by, Assigned_to_Doer,
               doer_task_id, row_created_at
        FROM `{self._table('Department_Tasks')}`
        WHERE Task_id = @task_id
        ORDER BY row_created_at DESC
        LIMIT 1
        """,
        job_config=job_config,
      ).result()
    )
    return self._department_assignment_from_row(rows[0]) if rows else None

  def list_current_department_assignments_for_department(self, department: str) -> dict[str, DepartmentTaskAssignment]:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("department", "STRING", department)]
    )
    rows = self._client.query(
      f"""
      SELECT dt.Task_id, dt.Task_brief, dt.comments, dt.Department_due_date, dt.Priority, dt.Assigned_by,
             dt.Assigned_to_Doer, dt.doer_task_id, dt.row_created_at
      FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY Task_id ORDER BY row_created_at DESC) AS rn
        FROM `{self._table('Department_Tasks')}`
      ) dt
      JOIN `{self._table('All_Tasks')}` a ON a.Task_id = dt.Task_id
      WHERE dt.rn = 1 AND a.Assigned_Department = @department
      """,
      job_config=job_config,
    ).result()
    return {row.Task_id: self._department_assignment_from_row(row) for row in rows}

  def list_current_department_assignments(self) -> dict[str, DepartmentTaskAssignment]:
    rows = self._client.query(
      f"""
      SELECT Task_id, Task_brief, comments, Department_due_date, Priority, Assigned_by, Assigned_to_Doer,
             doer_task_id, row_created_at
      FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY Task_id ORDER BY row_created_at DESC) AS rn
        FROM `{self._table('Department_Tasks')}`
      )
      WHERE rn = 1
      """
    ).result()
    return {row.Task_id: self._department_assignment_from_row(row) for row in rows}

  def list_current_assignments_for_doer(self, doer_email: str) -> list[DepartmentTaskAssignment]:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("doer_email", "STRING", doer_email.strip().lower())]
    )
    rows = self._client.query(
      f"""
      SELECT Task_id, Task_brief, comments, Department_due_date, Priority, Assigned_by, Assigned_to_Doer,
             doer_task_id, row_created_at
      FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY Task_id ORDER BY row_created_at DESC) AS rn
        FROM `{self._table('Department_Tasks')}`
      )
      WHERE rn = 1 AND LOWER(Assigned_to_Doer) = @doer_email
      ORDER BY row_created_at DESC
      """,
      job_config=job_config,
    ).result()
    return [self._department_assignment_from_row(row) for row in rows]

  def assign_department_task(
    self,
    *,
    task_id: str,
    task_brief: str,
    comments: str | None,
    department_due_date: str | None,
    priority: TaskPriority,
    assigned_by: str,
    assigned_to_doer: str,
  ) -> DepartmentTaskAssignment:
    doer_task_id = generate_prefixed_id("DT")
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("task_id", "STRING", task_id),
        bigquery.ScalarQueryParameter("task_brief", "STRING", task_brief),
        bigquery.ScalarQueryParameter("comments", "STRING", comments),
        bigquery.ScalarQueryParameter("due_date", "DATE", department_due_date),
        bigquery.ScalarQueryParameter("priority", "STRING", priority.value),
        bigquery.ScalarQueryParameter("assigned_by", "STRING", assigned_by.strip().lower()),
        bigquery.ScalarQueryParameter("assigned_to_doer", "STRING", assigned_to_doer.strip().lower()),
        bigquery.ScalarQueryParameter("doer_task_id", "STRING", doer_task_id),
      ]
    )
    self._client.query(
      f"""
      INSERT INTO `{self._table('Department_Tasks')}`
        (Task_id, Task_brief, comments, Department_due_date, Priority, Assigned_by, Assigned_to_Doer,
         doer_task_id, row_created_at)
      VALUES
        (@task_id, @task_brief, @comments, @due_date, @priority, @assigned_by, @assigned_to_doer,
         @doer_task_id, CURRENT_TIMESTAMP())
      """,
      job_config=job_config,
    ).result()

    # doer_task_id is born here; its first appearance is an auto-created "In Progress"
    # update row so the doer's task list shows it immediately, before anyone touches it.
    self.add_doer_task_update(
      task_id=task_id,
      doer_task_id=doer_task_id,
      task_update="In Progress",
      comments=None,
      submitted_by=assigned_by,
    )

    assignment = self.get_current_department_assignment(task_id)
    assert assignment is not None
    return assignment

  # -- Task_Revised ------------------------------------------------------------

  def revise_due_date(self, *, task_id: str, revised_date: str, revised_by: str) -> TaskRevision:
    revised_id = generate_prefixed_id("R")
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("task_id", "STRING", task_id),
        bigquery.ScalarQueryParameter("revised_id", "STRING", revised_id),
        bigquery.ScalarQueryParameter("revised_date", "DATE", revised_date),
        bigquery.ScalarQueryParameter("revised_by", "STRING", revised_by.strip().lower()),
      ]
    )
    self._client.query(
      f"""
      INSERT INTO `{self._table('Task_Revised')}`
        (Task_id, Revised_id, Revised_date, revised_by, Revised_date_time_stamp)
      VALUES (@task_id, @revised_id, @revised_date, @revised_by, CURRENT_TIMESTAMP())
      """,
      job_config=job_config,
    ).result()
    revisions = self.list_revisions_for_task(task_id)
    return next(r for r in revisions if r.revised_id == revised_id)

  def list_revisions_for_task(self, task_id: str) -> list[TaskRevision]:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("task_id", "STRING", task_id)]
    )
    rows = self._client.query(
      f"""
      SELECT Task_id, Revised_id, Revised_date, revised_by, Revised_date_time_stamp
      FROM `{self._table('Task_Revised')}` WHERE Task_id = @task_id ORDER BY Revised_date_time_stamp ASC
      """,
      job_config=job_config,
    ).result()
    return [self._revision_from_row(row) for row in rows]

  def list_revisions_for_tasks(self, task_ids: list[str]) -> dict[str, list[TaskRevision]]:
    if not task_ids:
      return {}
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ArrayQueryParameter("task_ids", "STRING", task_ids)]
    )
    rows = self._client.query(
      f"""
      SELECT Task_id, Revised_id, Revised_date, revised_by, Revised_date_time_stamp
      FROM `{self._table('Task_Revised')}` WHERE Task_id IN UNNEST(@task_ids)
      ORDER BY Revised_date_time_stamp ASC
      """,
      job_config=job_config,
    ).result()
    result: dict[str, list[TaskRevision]] = {}
    for row in rows:
      result.setdefault(row.Task_id, []).append(self._revision_from_row(row))
    return result

  # -- doer_Tasks (completion) & doer_Task_update (progress log) --------------

  def mark_doer_task_complete(
    self,
    *,
    task_id: str,
    doer_task_id: str,
    task_brief: str,
    assigned_by: str,
    submitted_by: str,
    comments: str | None,
    completion_date: str,
  ) -> DoerTaskCompletion:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("task_id", "STRING", task_id),
        bigquery.ScalarQueryParameter("task_brief", "STRING", task_brief),
        bigquery.ScalarQueryParameter("status", "STRING", DOER_TASK_STATUS_COMPLETED),
        bigquery.ScalarQueryParameter("completion_date", "DATE", completion_date),
        bigquery.ScalarQueryParameter("comments", "STRING", comments),
        bigquery.ScalarQueryParameter("assigned_by", "STRING", assigned_by.strip().lower()),
        bigquery.ScalarQueryParameter("doer_task_id", "STRING", doer_task_id),
        bigquery.ScalarQueryParameter("submitted_by", "STRING", submitted_by.strip().lower()),
      ]
    )
    self._client.query(
      f"""
      INSERT INTO `{self._table('doer_Tasks')}`
        (Task_id, Task_brief, Task_status, completion_date, Comments, Assigned_by, doer_task_id,
         submitted_by, row_created_at)
      VALUES
        (@task_id, @task_brief, @status, @completion_date, @comments, @assigned_by, @doer_task_id,
         @submitted_by, CURRENT_TIMESTAMP())
      """,
      job_config=job_config,
    ).result()
    completion = self.get_completion_for_doer_task(doer_task_id)
    assert completion is not None
    return completion

  def get_completion_for_doer_task(self, doer_task_id: str) -> DoerTaskCompletion | None:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("doer_task_id", "STRING", doer_task_id)]
    )
    rows = list(
      self._client.query(
        f"""
        SELECT Task_id, Task_brief, Task_status, completion_date, Comments, Assigned_by, doer_task_id, submitted_by
        FROM `{self._table('doer_Tasks')}` WHERE doer_task_id = @doer_task_id LIMIT 1
        """,
        job_config=job_config,
      ).result()
    )
    return self._doer_completion_from_row(rows[0]) if rows else None

  def get_completions_for_doer_tasks(self, doer_task_ids: list[str]) -> dict[str, DoerTaskCompletion]:
    if not doer_task_ids:
      return {}
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ArrayQueryParameter("doer_task_ids", "STRING", doer_task_ids)]
    )
    rows = self._client.query(
      f"""
      SELECT Task_id, Task_brief, Task_status, completion_date, Comments, Assigned_by, doer_task_id, submitted_by
      FROM `{self._table('doer_Tasks')}` WHERE doer_task_id IN UNNEST(@doer_task_ids)
      """,
      job_config=job_config,
    ).result()
    return {row.doer_task_id: self._doer_completion_from_row(row) for row in rows}

  def add_doer_task_update(
    self,
    *,
    task_id: str,
    doer_task_id: str,
    task_update: str,
    comments: str | None,
    submitted_by: str,
  ) -> DoerTaskUpdateEntry:
    task_update_id = generate_prefixed_id("U")
    job_config = bigquery.QueryJobConfig(
      query_parameters=[
        bigquery.ScalarQueryParameter("task_id", "STRING", task_id),
        bigquery.ScalarQueryParameter("task_update_id", "STRING", task_update_id),
        bigquery.ScalarQueryParameter("doer_task_id", "STRING", doer_task_id),
        bigquery.ScalarQueryParameter("task_update", "STRING", task_update),
        bigquery.ScalarQueryParameter("comments", "STRING", comments),
        bigquery.ScalarQueryParameter("submitted_by", "STRING", submitted_by.strip().lower()),
      ]
    )
    self._client.query(
      f"""
      INSERT INTO `{self._table('doer_Task_update')}`
        (Task_id, Task_Update_id, doer_task_id, Task_update, Comments, submitted_by, row_created_at)
      VALUES (@task_id, @task_update_id, @doer_task_id, @task_update, @comments, @submitted_by, CURRENT_TIMESTAMP())
      """,
      job_config=job_config,
    ).result()
    updates = self.list_updates_for_doer_task(doer_task_id)
    return next(u for u in updates if u.task_update_id == task_update_id)

  def list_updates_for_doer_task(self, doer_task_id: str) -> list[DoerTaskUpdateEntry]:
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ScalarQueryParameter("doer_task_id", "STRING", doer_task_id)]
    )
    rows = self._client.query(
      f"""
      SELECT Task_id, Task_Update_id, doer_task_id, Task_update, Comments, submitted_by, row_created_at
      FROM `{self._table('doer_Task_update')}` WHERE doer_task_id = @doer_task_id ORDER BY row_created_at ASC
      """,
      job_config=job_config,
    ).result()
    return [self._doer_update_from_row(row) for row in rows]

  def get_latest_updates_for_doer_tasks(self, doer_task_ids: list[str]) -> dict[str, DoerTaskUpdateEntry]:
    if not doer_task_ids:
      return {}
    job_config = bigquery.QueryJobConfig(
      query_parameters=[bigquery.ArrayQueryParameter("doer_task_ids", "STRING", doer_task_ids)]
    )
    rows = self._client.query(
      f"""
      SELECT Task_id, Task_Update_id, doer_task_id, Task_update, Comments, submitted_by, row_created_at
      FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY doer_task_id ORDER BY row_created_at DESC) AS rn
        FROM `{self._table('doer_Task_update')}`
        WHERE doer_task_id IN UNNEST(@doer_task_ids)
      )
      WHERE rn = 1
      """,
      job_config=job_config,
    ).result()
    return {row.doer_task_id: self._doer_update_from_row(row) for row in rows}

  # -- internals (v2 row parsing) ----------------------------------------------

  def _hr_table(self) -> str:
    return f"{self._project}.{self._settings.hr_dataset}.{self._settings.hr_employee_table}"

  def _user_from_row(self, row: bigquery.table.Row) -> UserAccount:
    try:
      role = UserRole(row.role)
    except ValueError:
      role = UserRole.MEMBER
    return UserAccount(user_id=row.user_id, name=row.name, role=role, active=bool(row.active))

  def _reporting_relation_from_row(self, row: bigquery.table.Row) -> ReportingRelation:
    return ReportingRelation(
      tl_name=row.TL_Name,
      manager_name=row.Manager_Name,
      emp_name=row.Emp_Name,
      emp_id=row.Emp_id,
      tl_email=row.TL_Email,
      manager_email=row.Manager_Email,
    )

  def _all_task_from_row(self, row: bigquery.table.Row) -> AllTask:
    return AllTask(
      task_id=row.Task_id,
      created_date_time=_to_timestamp_str(row.Created_date_time) or "",
      task_brief=row.Task_brief,
      assigned_department=row.Assigned_Department,
      ceo_task_due_date=_to_date_str(row.CEO_Task_due_date),
      priority=self._task_priority(row.Priority),
    )

  def _department_assignment_from_row(self, row: bigquery.table.Row) -> DepartmentTaskAssignment:
    return DepartmentTaskAssignment(
      task_id=row.Task_id,
      task_brief=row.Task_brief,
      comments=row.comments,
      department_due_date=_to_date_str(row.Department_due_date),
      priority=self._task_priority(row.Priority),
      assigned_by=row.Assigned_by,
      assigned_to_doer=row.Assigned_to_Doer,
      doer_task_id=row.doer_task_id,
      row_created_at=_to_timestamp_str(row.row_created_at) or "",
    )

  def _revision_from_row(self, row: bigquery.table.Row) -> TaskRevision:
    return TaskRevision(
      task_id=row.Task_id,
      revised_id=row.Revised_id,
      revised_date=_to_date_str(row.Revised_date) or "",
      revised_by=row.revised_by,
      revised_at=_to_timestamp_str(row.Revised_date_time_stamp) or "",
    )

  def _doer_completion_from_row(self, row: bigquery.table.Row) -> DoerTaskCompletion:
    return DoerTaskCompletion(
      task_id=row.Task_id,
      task_brief=row.Task_brief,
      task_status=row.Task_status,
      completion_date=_to_date_str(row.completion_date),
      comments=row.Comments,
      assigned_by=row.Assigned_by,
      doer_task_id=row.doer_task_id,
      submitted_by=row.submitted_by,
    )

  def _doer_update_from_row(self, row: bigquery.table.Row) -> DoerTaskUpdateEntry:
    return DoerTaskUpdateEntry(
      task_id=row.Task_id,
      task_update_id=row.Task_Update_id,
      doer_task_id=row.doer_task_id,
      task_update=row.Task_update,
      comments=row.Comments,
      submitted_by=row.submitted_by,
      row_created_at=_to_timestamp_str(row.row_created_at) or "",
    )

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
