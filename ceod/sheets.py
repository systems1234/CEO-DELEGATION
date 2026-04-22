from __future__ import annotations

import json
import logging
import re
from enum import IntEnum

import gspread
from google.oauth2.service_account import Credentials

from ceod.config import Settings
from ceod.exceptions import NotFoundError
from ceod.models import ParsedTaskAssignment, SeedProfile, TaskRecord, TaskStatus, TeamMember
from ceod.utils import build_random_seed_profiles, generate_row_id, normalise_whatsapp_number

MASTER_HEADERS = [
  "Assignee Name", "Assignee Number", "Task",
  "Assign Date", "Due Date", "New Date",
  "Postpone Reason", "Completion Date", "Status",
  "CEO Comments", "Other", "Row ID",
]
CONFIG_HEADERS = ["Name", "WhatsApp Number (E.164 digits only)", "Sheet Name (optional)"]
INDIVIDUAL_HEADERS = [
  "Task", "Assign Date", "Due Date",
  "New Date", "Postpone Reason", "Completion Date",
  "Status", "CEO Comments", "Other", "Row ID",
]
STATE_HEADERS = ["Key", "Value"]
CHATLOG_HEADERS = ["Timestamp", "Direction", "Number", "Name", "Message"]


class MasterCol(IntEnum):
  ASSIGNEE_NAME = 1
  ASSIGNEE_NUMBER = 2
  TASK = 3
  ASSIGN_DATE = 4
  DUE_DATE = 5
  NEW_DATE = 6
  POSTPONE_REASON = 7
  COMPLETION_DATE = 8
  STATUS = 9
  CEO_COMMENTS = 10
  OTHER = 11
  ROW_ID = 12


class IndividualCol(IntEnum):
  TASK = 1
  ASSIGN_DATE = 2
  DUE_DATE = 3
  NEW_DATE = 4
  POSTPONE_REASON = 5
  COMPLETION_DATE = 6
  STATUS = 7
  CEO_COMMENTS = 8
  OTHER = 9
  ROW_ID = 10


class GoogleSheetsRepository:
  def __init__(self, settings: Settings) -> None:
    self._settings = settings
    self._logger = logging.getLogger(__name__)
    credentials = self._build_credentials(settings)
    client = gspread.authorize(credentials)
    self._spreadsheet = client.open_by_key(settings.spreadsheet_id)
    self._sheet_cache: dict[str, gspread.Worksheet] = {}

  def _warm_cache(self) -> None:
    if not self._sheet_cache:
      for ws in self._spreadsheet.worksheets():
        self._sheet_cache[ws.title] = ws

  def ensure_base_sheets(self) -> None:
    self._warm_cache()
    self._ensure_sheet("Master", MASTER_HEADERS)
    self._ensure_sheet("Config", CONFIG_HEADERS)
    self._ensure_sheet("RuntimeState", STATE_HEADERS)
    self._ensure_sheet("ChatLog", CHATLOG_HEADERS)

  def get_team_members(self) -> list[TeamMember]:
    self.ensure_base_sheets()
    sheet = self._get_sheet("Config")
    rows = sheet.get_all_values()
    members: list[TeamMember] = []
    for row in rows[1:]:
      if len(row) < 2 or not row[0] or not row[1]:
        continue
      name = row[0].strip()
      number = normalise_whatsapp_number(row[1])
      sheet_name = (row[2].strip() if len(row) > 2 and row[2].strip() else name)
      members.append(TeamMember(name=name, number=number, sheet_name=sheet_name))
    return members

  def get_member_by_name(self, name: str) -> TeamMember | None:
    lower_name = name.strip().lower()
    for member in self.get_team_members():
      if member.name.lower() == lower_name:
        return member
    return None

  def create_task(self, parsed: ParsedTaskAssignment, today_iso: str) -> str:
    member = self.get_member_by_name(parsed.assignee)
    if member is None:
      raise NotFoundError(f'Unknown assignee "{parsed.assignee}"')

    row_id = generate_row_id()
    master = self._get_sheet("Master")
    master.append_row(
      [
        member.name,
        member.number,
        parsed.task,
        today_iso,
        parsed.due_date or "",
        "",
        "",
        "",
        TaskStatus.PENDING.value,
        "",
        "",
        row_id,
      ],
      value_input_option="RAW",
    )

    individual = self._ensure_sheet(member.sheet_name, INDIVIDUAL_HEADERS)
    individual.append_row(
      [
        parsed.task,
        today_iso,
        parsed.due_date or "",
        "",
        "",
        "",
        TaskStatus.PENDING.value,
        "",
        "",
        row_id,
      ],
      value_input_option="RAW",
    )
    return row_id

  def get_task_by_row_id(self, row_id: str) -> TaskRecord | None:
    rows = self._get_sheet("Master").get_all_values()
    for row in rows[1:]:
      if self._is_valid_master_row(row) and row[MasterCol.ROW_ID - 1] == row_id:
        return self._task_from_master_row(row)
    return None

  def get_latest_pending_task_for_number(self, number: str) -> TaskRecord | None:
    clean_number = normalise_whatsapp_number(number)
    rows = self._get_sheet("Master").get_all_values()
    for row in reversed(rows[1:]):
      if not self._is_valid_master_row(row):
        continue
      status = self._task_status(row[MasterCol.STATUS - 1])
      if row[MasterCol.ASSIGNEE_NUMBER - 1] == clean_number and status != TaskStatus.DONE:
        return self._task_from_master_row(row)
    return None

  def list_tasks(self) -> list[TaskRecord]:
    rows = self._get_sheet("Master").get_all_values()
    tasks: list[TaskRecord] = []
    for row in rows[1:]:
      if not self._is_valid_master_row(row):
        continue
      tasks.append(self._task_from_master_row(row))
    return tasks

  def mark_task_done(self, row_id: str, completion_date: str) -> None:
    self._update_task_fields(
      row_id=row_id,
      master_updates={
        MasterCol.STATUS: TaskStatus.DONE.value,
        MasterCol.COMPLETION_DATE: completion_date,
      },
      individual_updates={
        IndividualCol.STATUS: TaskStatus.DONE.value,
        IndividualCol.COMPLETION_DATE: completion_date,
      },
    )

  def postpone_task(self, row_id: str, new_date: str, reason: str) -> None:
    self._update_task_fields(
      row_id=row_id,
      master_updates={
        MasterCol.STATUS: TaskStatus.POSTPONED.value,
        MasterCol.NEW_DATE: new_date,
        MasterCol.POSTPONE_REASON: reason,
      },
      individual_updates={
        IndividualCol.STATUS: TaskStatus.POSTPONED.value,
        IndividualCol.NEW_DATE: new_date,
        IndividualCol.POSTPONE_REASON: reason,
      },
    )

  def get_tasks_due_today(self, today_iso: str) -> list[TaskRecord]:
    due_tasks: list[TaskRecord] = []
    rows = self._get_sheet("Master").get_all_values()
    for row in rows[1:]:
      if not self._is_valid_master_row(row):
        continue
      task = self._task_from_master_row(row)
      if task.status == TaskStatus.DONE:
        continue
      is_due_today = task.status == TaskStatus.PENDING and task.due_date == today_iso
      is_postponed_due_today = task.status == TaskStatus.POSTPONED and task.new_date == today_iso
      if is_due_today or is_postponed_due_today:
        due_tasks.append(task)
    return due_tasks

  def mark_overdue_tasks(self, today_iso: str) -> list[TaskRecord]:
    overdue_tasks: list[TaskRecord] = []
    rows = self._get_sheet("Master").get_all_values()
    for row in rows[1:]:
      if not self._is_valid_master_row(row):
        continue
      task = self._task_from_master_row(row)
      if task.status not in {TaskStatus.PENDING, TaskStatus.POSTPONED}:
        continue
      effective_due = task.effective_due_date
      if not effective_due or effective_due >= today_iso:
        continue
      self._update_task_fields(
        row_id=task.row_id,
        master_updates={MasterCol.STATUS: TaskStatus.OVERDUE.value},
        individual_updates={IndividualCol.STATUS: TaskStatus.OVERDUE.value},
      )
      overdue_tasks.append(
        TaskRecord(
          row_id=task.row_id,
          assignee_name=task.assignee_name,
          assignee_number=task.assignee_number,
          task=task.task,
          status=TaskStatus.OVERDUE,
          assign_date=task.assign_date,
          due_date=task.due_date,
          new_date=task.new_date,
          postpone_reason=task.postpone_reason,
        )
      )
    return overdue_tasks

  def set_pending_postpone(self, number: str, row_id: str) -> None:
    key = self._pending_postpone_key(number)
    sheet = self._get_sheet("RuntimeState")
    rows = sheet.get_all_values()
    for index, row in enumerate(rows[1:], start=2):
      if len(row) >= 1 and row[0] == key:
        sheet.update_cell(index, 2, row_id)
        return
    sheet.append_row([key, row_id], value_input_option="RAW")

  def get_pending_postpone(self, number: str) -> str | None:
    key = self._pending_postpone_key(number)
    rows = self._get_sheet("RuntimeState").get_all_values()
    for row in rows[1:]:
      if len(row) >= 2 and row[0] == key:
        return row[1] or None
    return None

  def clear_pending_postpone(self, number: str) -> None:
    key = self._pending_postpone_key(number)
    sheet = self._get_sheet("RuntimeState")
    rows = sheet.get_all_values()
    for index, row in enumerate(rows[1:], start=2):
      if len(row) >= 1 and row[0] == key:
        sheet.delete_rows(index)
        return

  def set_pending_commitment(self, number: str, row_id: str) -> None:
    key = self._pending_commitment_key(number)
    sheet = self._get_sheet("RuntimeState")
    rows = sheet.get_all_values()
    for index, row in enumerate(rows[1:], start=2):
      if len(row) >= 1 and row[0] == key:
        sheet.update_cell(index, 2, row_id)
        return
    sheet.append_row([key, row_id], value_input_option="RAW")

  def get_pending_commitment(self, number: str) -> str | None:
    key = self._pending_commitment_key(number)
    rows = self._get_sheet("RuntimeState").get_all_values()
    for row in rows[1:]:
      if len(row) >= 2 and row[0] == key:
        return row[1] or None
    return None

  def clear_pending_commitment(self, number: str) -> None:
    key = self._pending_commitment_key(number)
    sheet = self._get_sheet("RuntimeState")
    rows = sheet.get_all_values()
    for index, row in enumerate(rows[1:], start=2):
      if len(row) >= 1 and row[0] == key:
        sheet.delete_rows(index)
        return

  def commit_task_due_date(self, row_id: str, due_date: str) -> None:
    master = self._get_sheet("Master")
    rows = master.get_all_values()
    assignee_name: str | None = None
    for index, row in enumerate(rows[1:], start=2):
      if len(row) >= MasterCol.ROW_ID and row[MasterCol.ROW_ID - 1] == row_id:
        assignee_name = row[MasterCol.ASSIGNEE_NAME - 1]
        master.update_cell(index, int(MasterCol.DUE_DATE), due_date)
        break
    if assignee_name is None:
      raise NotFoundError(f"Task {row_id} not found in Master")
    member = self.get_member_by_name(assignee_name)
    if member is None:
      return
    individual = self._ensure_sheet(member.sheet_name, INDIVIDUAL_HEADERS)
    rows = individual.get_all_values()
    for index, row in enumerate(rows[1:], start=2):
      if len(row) >= IndividualCol.ROW_ID and row[IndividualCol.ROW_ID - 1] == row_id:
        individual.update_cell(index, int(IndividualCol.DUE_DATE), due_date)
        return

  def log_message(self, timestamp: str, direction: str, number: str, name: str, text: str) -> None:
    sheet = self._get_sheet("ChatLog")
    sheet.append_row([timestamp, direction, number, name, text[:50000]], value_input_option="RAW")

  def get_chat_log(self) -> list[dict[str, str]]:
    rows = self._get_sheet("ChatLog").get_all_values()
    entries: list[dict[str, str]] = []
    for row in rows[1:]:
      if len(row) < 5 or not row[0]:
        continue
      entries.append({
        "timestamp": row[0],
        "direction": row[1],
        "number": row[2],
        "name": row[3],
        "text": row[4],
      })
    return entries

  def seed_random_test_profiles(self, count: int) -> list[SeedProfile]:
    members = self.get_team_members()
    existing_names = {member.name.lower() for member in members}
    existing_numbers = {member.number for member in members}
    existing_sheet_names = {member.sheet_name.lower() for member in members}
    profiles = build_random_seed_profiles(count, existing_names, existing_numbers, existing_sheet_names)

    config = self._get_sheet("Config")
    # Fetch worksheet list once to avoid a metadata API call per profile
    known_titles: set[str] = {ws.title for ws in self._spreadsheet.worksheets()}
    for profile in profiles:
      config.append_row([profile.name, profile.number, profile.sheet_name], value_input_option="RAW")
      if profile.sheet_name not in known_titles:
        sheet = self._spreadsheet.add_worksheet(
          title=profile.sheet_name, rows=1000, cols=max(len(INDIVIDUAL_HEADERS), 12)
        )
        sheet.append_row(INDIVIDUAL_HEADERS, value_input_option="RAW")
        sheet.freeze(rows=1)
        known_titles.add(profile.sheet_name)
    return profiles

  def _build_credentials(self, settings: Settings) -> Credentials:
    scopes = [
      "https://www.googleapis.com/auth/spreadsheets",
      "https://www.googleapis.com/auth/drive",
    ]
    if settings.google_service_account_json:
      info = json.loads(settings.google_service_account_json)
      return Credentials.from_service_account_info(info, scopes=scopes)
    return Credentials.from_service_account_file(settings.google_application_credentials, scopes=scopes)

  def _pending_postpone_key(self, number: str) -> str:
    return f"POSTPONE_PENDING_{normalise_whatsapp_number(number)}"

  def _pending_commitment_key(self, number: str) -> str:
    return f"COMMITMENT_PENDING_{normalise_whatsapp_number(number)}"

  def _get_sheet(self, name: str) -> gspread.Worksheet:
    if name in self._sheet_cache:
      return self._sheet_cache[name]
    try:
      sheet = self._spreadsheet.worksheet(name)
      self._sheet_cache[name] = sheet
      return sheet
    except gspread.WorksheetNotFound as exc:
      raise NotFoundError(f'Worksheet "{name}" not found') from exc

  def _ensure_sheet(self, name: str, headers: list[str]) -> gspread.Worksheet:
    if name in self._sheet_cache:
      return self._sheet_cache[name]
    try:
      sheet = self._spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
      sheet = self._spreadsheet.add_worksheet(title=name, rows=1000, cols=max(len(headers), 12))
    self._sheet_cache[name] = sheet
    values = sheet.get_all_values()
    if not values:
      sheet.append_row(headers, value_input_option="RAW")
      sheet.freeze(rows=1)
    return sheet

  def _update_task_fields(
    self,
    row_id: str,
    master_updates: dict[IntEnum, str],
    individual_updates: dict[IntEnum, str],
  ) -> None:
    master = self._get_sheet("Master")
    rows = master.get_all_values()
    assignee_name: str | None = None
    for index, row in enumerate(rows[1:], start=2):
      if len(row) >= MasterCol.ROW_ID and row[MasterCol.ROW_ID - 1] == row_id:
        assignee_name = row[MasterCol.ASSIGNEE_NAME - 1]
        for column, value in master_updates.items():
          master.update_cell(index, int(column), value)
        break
    if assignee_name is None:
      raise NotFoundError(f"Task row {row_id} not found in Master")

    member = self.get_member_by_name(assignee_name)
    if member is None:
      raise NotFoundError(f'Assignee "{assignee_name}" not found in Config')
    individual = self._ensure_sheet(member.sheet_name, INDIVIDUAL_HEADERS)
    rows = individual.get_all_values()
    for index, row in enumerate(rows[1:], start=2):
      if len(row) >= IndividualCol.ROW_ID and row[IndividualCol.ROW_ID - 1] == row_id:
        for column, value in individual_updates.items():
          individual.update_cell(index, int(column), value)
        return
    raise NotFoundError(f"Task row {row_id} not found in sheet {member.sheet_name}")

  def _task_from_master_row(self, row: list[str]) -> TaskRecord:
    return TaskRecord(
      row_id=row[MasterCol.ROW_ID - 1],
      assignee_name=row[MasterCol.ASSIGNEE_NAME - 1],
      assignee_number=row[MasterCol.ASSIGNEE_NUMBER - 1],
      task=row[MasterCol.TASK - 1],
      status=self._task_status(row[MasterCol.STATUS - 1]),
      assign_date=row[MasterCol.ASSIGN_DATE - 1] or None,
      due_date=row[MasterCol.DUE_DATE - 1] or None,
      new_date=row[MasterCol.NEW_DATE - 1] or None,
      postpone_reason=row[MasterCol.POSTPONE_REASON - 1] or None,
      completion_date=row[MasterCol.COMPLETION_DATE - 1] or None,
    )

  _ROW_ID_RE = re.compile(r"^T\d{13}[0-9A-F]{4}$")

  def _is_valid_master_row(self, row: list[str]) -> bool:
    if len(row) < MasterCol.ROW_ID:
      return False
    return bool(self._ROW_ID_RE.match(row[MasterCol.ROW_ID - 1]))

  def _task_status(self, raw: str) -> TaskStatus:
    return TaskStatus(raw or TaskStatus.PENDING.value)
