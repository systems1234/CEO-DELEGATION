from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from ceod.app import create_app
from ceod.models import DashboardSnapshot, DashboardStats, IncomingMessage, ParsedPostponeReply, ParsedTaskAssignment, SeedProfile, TaskPriority, TaskRecord, TaskStatus, TeamMember
from ceod.service import TaskDelegationService
from ceod.utils import today_iso


class FakeParser:
  def __init__(self) -> None:
    self.assignment = ParsedTaskAssignment(
      assignee="Rahul",
      task="Prepare sales report",
      due_date="2026-04-22",
    )
    self.postpone = ParsedPostponeReply(new_date="2026-04-25", reason="client delay")

  def parse_task_assignment(self, raw_message: str, known_names: list[str]) -> ParsedTaskAssignment | None:
    return self.assignment if "rahul" in raw_message.lower() else None

  def parse_postpone_reply(self, reply_text: str) -> ParsedPostponeReply:
    return self.postpone


class FakeWhatsAppGateway:
  def __init__(self) -> None:
    self.messages: list[tuple[str, str]] = []
    self.files: list[tuple[str, str, str, str]] = []

  def send_text(self, to_number: str, body: str) -> str:
    self.messages.append((to_number, body))
    return f"msg-{len(self.messages)}"

  def send_file(self, to_number: str, file_bytes: bytes, filename: str, mime_type: str, caption: str = "") -> str:
    self.files.append((to_number, filename, mime_type, caption))
    return f"file-{len(self.files)}"

  def verify_get(self, params: object) -> tuple[bool, str]:
    return True, "ok"

  def parse_incoming_message(self, payload: object) -> None:
    return None


class FakeRepository:
  def __init__(self) -> None:
    self.members = [TeamMember(name="Rahul", number="919000000001")]
    self.tasks: dict[str, TaskRecord] = {}
    self.pending_postpone: dict[str, str] = {}
    self.pending_commitment: dict[str, str] = {}
    self.chat_log: list[dict[str, str]] = []
    self.seed_profiles: list[SeedProfile] = []

  def get_team_members(self) -> list[TeamMember]:
    return list(self.members)

  def get_member_by_name(self, name: str) -> TeamMember | None:
    for member in self.members:
      if member.name.lower() == name.lower():
        return member
    return None

  def get_member_by_email(self, email: str) -> TeamMember | None:
    for member in self.members:
      if member.email and member.email.lower() == email.lower():
        return member
    return None

  def create_task(self, parsed: ParsedTaskAssignment, today_iso: str, priority: TaskPriority = TaskPriority.MEDIUM) -> str:
    row_id = f"T{len(self.tasks) + 1:017d}ABCD"
    member = self.get_member_by_name(parsed.assignee)
    assert member is not None
    self.tasks[row_id] = TaskRecord(
      row_id=row_id,
      assignee_name=member.name,
      assignee_number=member.number,
      task=parsed.task,
      status=TaskStatus.PENDING,
      priority=priority,
      assign_date=today_iso,
      due_date=parsed.due_date,
    )
    return row_id

  def get_task_by_row_id(self, row_id: str) -> TaskRecord | None:
    return self.tasks.get(row_id)

  def get_latest_pending_task_for_number(self, number: str) -> TaskRecord | None:
    for task in reversed(list(self.tasks.values())):
      if task.assignee_number == number and task.status != TaskStatus.DONE:
        return task
    return None

  def list_tasks(self) -> list[TaskRecord]:
    return list(self.tasks.values())

  def mark_task_done(self, row_id: str, completion_date: str) -> None:
    task = self.tasks[row_id]
    self.tasks[row_id] = replace(task, status=TaskStatus.DONE, completion_date=completion_date)

  def postpone_task(self, row_id: str, new_date: str, reason: str) -> None:
    task = self.tasks[row_id]
    self.tasks[row_id] = replace(
      task,
      status=TaskStatus.POSTPONED,
      new_date=new_date,
      postpone_reason=reason,
    )

  def get_tasks_due_today(self, today_iso: str) -> list[TaskRecord]:
    return [
      task
      for task in self.tasks.values()
      if (task.status == TaskStatus.PENDING and task.due_date == today_iso)
      or (task.status == TaskStatus.POSTPONED and task.new_date == today_iso)
    ]

  def mark_overdue_tasks(self, today_iso: str) -> list[TaskRecord]:
    updated: list[TaskRecord] = []
    for row_id, task in list(self.tasks.items()):
      effective_due = task.effective_due_date
      if task.status in {TaskStatus.PENDING, TaskStatus.POSTPONED} and effective_due and effective_due < today_iso:
        self.tasks[row_id] = replace(task, status=TaskStatus.OVERDUE)
        updated.append(self.tasks[row_id])
    return updated

  def set_pending_postpone(self, number: str, row_id: str) -> None:
    self.pending_postpone[number] = row_id

  def get_pending_postpone(self, number: str) -> str | None:
    return self.pending_postpone.get(number)

  def clear_pending_postpone(self, number: str) -> None:
    self.pending_postpone.pop(number, None)

  def set_pending_commitment(self, number: str, row_id: str) -> None:
    self.pending_commitment[number] = row_id

  def get_pending_commitment(self, number: str) -> str | None:
    return self.pending_commitment.get(number)

  def clear_pending_commitment(self, number: str) -> None:
    self.pending_commitment.pop(number, None)

  def commit_task_due_date(self, row_id: str, due_date: str) -> None:
    task = self.tasks[row_id]
    self.tasks[row_id] = replace(task, due_date=due_date)

  def log_message(self, timestamp: str, direction: str, number: str, name: str, text: str) -> None:
    self.chat_log.append(
      {
        "timestamp": timestamp,
        "direction": direction,
        "number": number,
        "name": name,
        "text": text,
      }
    )

  def get_chat_log(self) -> list[dict[str, str]]:
    return list(self.chat_log)

  def add_team_member(self, name: str, number: str, email: str = "") -> TeamMember:
    member = TeamMember(name=name, number=number, email=email or None)
    self.members.append(member)
    return member

  def seed_random_test_profiles(self, count: int) -> list[SeedProfile]:
    self.seed_profiles = [
      SeedProfile(name=f"User {index}", number=f"919000000{index:03d}")
      for index in range(count)
    ]
    return list(self.seed_profiles)


class TaskDelegationServiceTests(unittest.TestCase):
  def setUp(self) -> None:
    self.parser = FakeParser()
    self.gateway = FakeWhatsAppGateway()
    self.repository = FakeRepository()
    self.service = TaskDelegationService(
      parser=self.parser,
      whatsapp=self.gateway,
      repository=self.repository,
      ceo_number="919999999999",
      timezone_name="Asia/Kolkata",
    )

  def test_ceo_assignment_creates_task_and_sends_two_messages(self) -> None:
    self.service.handle_incoming_message(IncomingMessage(from_number="919999999999", text="Rahul ko report bhejo"))

    self.assertEqual(len(self.repository.tasks), 1)
    self.assertEqual(len(self.gateway.messages), 2)
    task = next(iter(self.repository.tasks.values()))
    self.assertEqual(task.task, "Prepare sales report")
    self.assertEqual(task.status, TaskStatus.PENDING)

  def test_done_reply_marks_task_done(self) -> None:
    self.service.handle_incoming_message(IncomingMessage(from_number="919999999999", text="Rahul ko report bhejo"))
    row_id = next(iter(self.repository.tasks.keys()))
    self.service.handle_incoming_message(IncomingMessage(from_number="919000000001", text="25/04/2026"))

    self.service.handle_incoming_message(IncomingMessage(from_number="919000000001", text=f"Done {row_id}"))

    self.assertEqual(self.repository.tasks[row_id].status, TaskStatus.DONE)
    self.assertEqual(len(self.gateway.messages), 6)

  def test_postpone_flow_uses_pending_state_before_generic_postpone_match(self) -> None:
    self.service.handle_incoming_message(IncomingMessage(from_number="919999999999", text="Rahul ko report bhejo"))
    row_id = next(iter(self.repository.tasks.keys()))
    self.service.handle_incoming_message(IncomingMessage(from_number="919000000001", text="25/04/2026"))

    self.service.handle_incoming_message(IncomingMessage(from_number="919000000001", text=f"Postpone {row_id}"))
    self.assertEqual(self.repository.get_pending_postpone("919000000001"), row_id)

    self.service.handle_incoming_message(
      IncomingMessage(
        from_number="919000000001",
        text="NEW DATE: 25/04/2026 | REASON: client delay",
      )
    )

    self.assertEqual(self.repository.tasks[row_id].status, TaskStatus.POSTPONED)
    self.assertEqual(self.repository.tasks[row_id].new_date, "2026-04-25")
    self.assertIsNone(self.repository.get_pending_postpone("919000000001"))

  def test_daily_follow_up_sends_due_today_and_overdue_notifications(self) -> None:
    self.repository.tasks["T1"] = TaskRecord(
      row_id="T1",
      assignee_name="Rahul",
      assignee_number="919000000001",
      task="Due today",
      status=TaskStatus.PENDING,
      due_date="2026-04-22",
    )
    self.repository.tasks["T2"] = TaskRecord(
      row_id="T2",
      assignee_name="Rahul",
      assignee_number="919000000001",
      task="Past postponed date",
      status=TaskStatus.POSTPONED,
      due_date="2026-04-18",
      new_date="2026-04-20",
    )

    self.service.daily_follow_up_check()

    self.assertEqual(self.repository.tasks["T2"].status, TaskStatus.OVERDUE)
    self.assertEqual(len(self.gateway.messages), 3)

  def test_seed_profiles_delegates_to_repository(self) -> None:
    profiles = self.service.seed_random_test_profiles(10)
    self.assertEqual(len(profiles), 10)

  def test_assign_task_from_frontend_notifies_assignee(self) -> None:
    task = self.service.assign_task(assignee="Rahul", task="Call supplier", due_date="2026-04-24")
    self.assertEqual(task.task, "Call supplier")
    self.assertEqual(task.due_date, "2026-04-24")
    self.assertEqual(len(self.gateway.messages), 1)
    self.assertIn("you have been assigned a new task", self.gateway.messages[0][1])
    self.assertEqual(self.repository.get_pending_commitment("919000000001"), task.row_id)

  def test_dashboard_assigned_task_accepts_commitment_reply_with_context(self) -> None:
    task = self.service.assign_task(assignee="Rahul", task="Call supplier", due_date="2026-04-24")

    self.service.handle_incoming_message(
      IncomingMessage(
        from_number="919000000001",
        text=f"I will finish this by 25/04/2026. Ref: {task.row_id}",
      )
    )

    self.assertEqual(self.repository.tasks[task.row_id].due_date, "2026-04-25")
    self.assertIsNone(self.repository.get_pending_commitment("919000000001"))

  def test_ceo_assignment_falls_back_for_number_mentions_when_parser_returns_none(self) -> None:
    self.parser.assignment = None

    self.service.handle_incoming_message(
      IncomingMessage(from_number="919999999999", text="@919000000001 Test a testing task.")
    )

    task = next(iter(self.repository.tasks.values()))
    self.assertEqual(task.assignee_name, "Rahul")
    self.assertEqual(task.task, "Test a testing task.")

  def test_complete_task_marks_done_and_notifies(self) -> None:
    task = self.service.assign_task(assignee="Rahul", task="Ship the deck", due_date="2026-04-24")
    updated = self.service.complete_task(row_id=task.row_id)
    self.assertEqual(updated.status, TaskStatus.DONE)
    self.assertEqual(self.repository.tasks[task.row_id].status, TaskStatus.DONE)

  def test_complete_task_unknown_row_id_raises(self) -> None:
    with self.assertRaises(ValueError):
      self.service.complete_task(row_id="does-not-exist")

  def test_postpone_task_from_dashboard_reschedules_and_notifies(self) -> None:
    task = self.service.assign_task(assignee="Rahul", task="Ship the deck", due_date="2026-04-24")
    updated = self.service.postpone_task_from_dashboard(row_id=task.row_id, new_date="2026-05-01", reason="Waiting on client")
    self.assertEqual(updated.status, TaskStatus.POSTPONED)
    self.assertEqual(updated.new_date, "2026-05-01")
    self.assertTrue(any("rescheduled" in body for _, body in self.gateway.messages))

  def test_postpone_task_from_dashboard_requires_reason(self) -> None:
    task = self.service.assign_task(assignee="Rahul", task="Ship the deck", due_date="2026-04-24")
    with self.assertRaises(ValueError):
      self.service.postpone_task_from_dashboard(row_id=task.row_id, new_date="2026-05-01", reason="  ")

  def test_get_my_tasks_filters_by_linked_email(self) -> None:
    self.repository.members[0] = TeamMember(name="Rahul", number="919000000001", email="rahul@example.com")
    self.service.assign_task(assignee="Rahul", task="Task A", due_date="2026-04-24")
    self.service.assign_task(assignee="Rahul", task="Task B", due_date="2026-04-25")

    tasks = self.service.get_my_tasks("rahul@example.com")
    self.assertEqual(len(tasks), 2)
    self.assertEqual(self.service.get_my_tasks("unknown@example.com"), [])

  def test_get_triage_view_groups_overdue_by_date_and_priority(self) -> None:
    self.repository.tasks["T1"] = TaskRecord(
      row_id="T1", assignee_name="Rahul", assignee_number="919000000001",
      task="Overdue low", status=TaskStatus.PENDING, priority=TaskPriority.LOW, due_date="2020-01-01",
    )
    self.repository.tasks["T2"] = TaskRecord(
      row_id="T2", assignee_name="Rahul", assignee_number="919000000001",
      task="Overdue critical", status=TaskStatus.PENDING, priority=TaskPriority.CRITICAL, due_date="2020-01-01",
    )
    self.repository.tasks["T3"] = TaskRecord(
      row_id="T3", assignee_name="Rahul", assignee_number="919000000001",
      task="Due today", status=TaskStatus.PENDING, priority=TaskPriority.MEDIUM,
      due_date=today_iso("Asia/Kolkata"),
    )

    view = self.service.get_triage_view()

    self.assertEqual(len(view.overdue_groups), 1)
    self.assertEqual(view.overdue_groups[0].date, "2020-01-01")
    self.assertEqual([t.row_id for t in view.overdue_groups[0].tasks], ["T2", "T1"])
    self.assertEqual([t.row_id for t in view.due_today], ["T3"])

  def test_get_kpi_report_computes_completion_rate(self) -> None:
    self.repository.tasks["T1"] = TaskRecord(
      row_id="T1", assignee_name="Rahul", assignee_number="919000000001",
      task="Done one", status=TaskStatus.DONE, assign_date="2026-04-01", completion_date="2026-04-03",
    )
    self.repository.tasks["T2"] = TaskRecord(
      row_id="T2", assignee_name="Rahul", assignee_number="919000000001",
      task="Overdue one", status=TaskStatus.OVERDUE, due_date="2026-04-01",
    )

    report = self.service.get_kpi_report()

    self.assertEqual(report.total_tasks, 2)
    self.assertEqual(report.completed_tasks, 1)
    self.assertEqual(report.overdue_tasks, 1)
    self.assertEqual(report.completion_rate, 50.0)
    self.assertEqual(report.avg_completion_days, 2.0)
    self.assertEqual(len(report.daily_trend), 14)
    self.assertEqual(report.members[0].name, "Rahul")
    self.assertEqual(report.members[0].assigned, 2)

  def test_dashboard_snapshot_counts_tasks(self) -> None:
    self.repository.tasks["T1"] = TaskRecord(
      row_id="T1",
      assignee_name="Rahul",
      assignee_number="919000000001",
      task="Pending item",
      status=TaskStatus.PENDING,
      due_date="2026-04-22",
    )
    self.repository.tasks["T2"] = TaskRecord(
      row_id="T2",
      assignee_name="Rahul",
      assignee_number="919000000001",
      task="Done item",
      status=TaskStatus.DONE,
      due_date="2026-04-21",
    )

    snapshot = self.service.get_dashboard_snapshot()
    self.assertEqual(snapshot.stats.total_tasks, 2)
    self.assertEqual(snapshot.stats.active_tasks, 1)
    self.assertEqual(snapshot.stats.completed_tasks, 1)
    self.assertEqual(snapshot.stats.team_members, 1)


class DashboardAppTests(unittest.TestCase):
  def setUp(self) -> None:
    self.parser = FakeParser()
    self.gateway = FakeWhatsAppGateway()
    self.repository = FakeRepository()
    self.service = TaskDelegationService(
      parser=self.parser,
      whatsapp=self.gateway,
      repository=self.repository,
      ceo_number="919999999999",
      timezone_name="Asia/Kolkata",
    )
    self.service.assign_task(assignee="Rahul", task="Existing dashboard task", due_date="2026-04-24")
    self.session_secret = "test-session-secret"
    fake_container = SimpleNamespace(
      settings=SimpleNamespace(
        script_timezone="Asia/Kolkata",
        dashboard_auth_enabled=True,
        google_oauth_client_id=None,
        google_oauth_client_secret=None,
        allowed_google_domain="example.com",
        allowed_google_emails=None,
        session_secret=self.session_secret,
        public_base_url=None,
        cron_secret="test-cron-secret",
      ),
      service=self.service,
      whatsapp=self.gateway,
      repository=self.repository,
      close=lambda: None,
    )
    self.client = TestClient(create_app(container=fake_container))
    session_token = URLSafeTimedSerializer(self.session_secret, salt="ceod-session").dumps("ceo@example.com")
    self.client.cookies.set("ceod_session", session_token)

  def test_dashboard_page_requires_auth(self) -> None:
    anonymous_client = TestClient(self.client.app, follow_redirects=False)
    response = anonymous_client.get("/")
    self.assertEqual(response.status_code, 302)
    self.assertTrue(response.headers["location"].startswith("/login"))

  def test_dashboard_page_loads(self) -> None:
    response = self.client.get("/")
    self.assertEqual(response.status_code, 200)
    self.assertIn("Mission Control", response.text)

  def test_board_page_loads(self) -> None:
    response = self.client.get("/board")
    self.assertEqual(response.status_code, 200)
    self.assertIn("Live board", response.text)

  def test_my_tasks_page_loads(self) -> None:
    response = self.client.get("/my-tasks")
    self.assertEqual(response.status_code, 200)

  def test_reports_page_loads(self) -> None:
    response = self.client.get("/reports")
    self.assertEqual(response.status_code, 200)

  def test_chat_page_loads(self) -> None:
    response = self.client.get("/chat")
    self.assertEqual(response.status_code, 200)
    self.assertIn("Chats", response.text)

  def test_dashboard_api_returns_snapshot(self) -> None:
    response = self.client.get("/api/dashboard")
    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertEqual(payload["stats"]["total_tasks"], 1)
    self.assertEqual(payload["members"][0]["name"], "Rahul")

  def test_chat_api_returns_conversations(self) -> None:
    response = self.client.get("/api/chats")
    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertEqual(payload["conversations"][0]["number"], "919000000001")

  def test_assign_task_api_creates_task(self) -> None:
    response = self.client.post(
      "/api/tasks",
      json={"assignee": "Rahul", "task": "Frontend-created task", "due_date": "2026-04-30"},
    )
    self.assertEqual(response.status_code, 201)
    payload = response.json()
    self.assertEqual(payload["task"]["task"], "Frontend-created task")
    self.assertEqual(len(self.repository.tasks), 2)

  def test_mark_task_done_api(self) -> None:
    task_row_id = next(iter(self.repository.tasks.keys()))
    response = self.client.post(f"/api/tasks/{task_row_id}/done")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["task"]["status"], "Done")

  def test_mark_task_done_api_unknown_row_id_returns_404(self) -> None:
    response = self.client.post("/api/tasks/does-not-exist/done")
    self.assertEqual(response.status_code, 404)

  def test_postpone_task_api(self) -> None:
    task_row_id = next(iter(self.repository.tasks.keys()))
    response = self.client.post(
      f"/api/tasks/{task_row_id}/postpone",
      json={"new_date": "2026-05-05", "reason": "Vendor delay"},
    )
    self.assertEqual(response.status_code, 200)
    payload = response.json()["task"]
    self.assertEqual(payload["status"], "Postponed")
    self.assertEqual(payload["new_date"], "2026-05-05")

  def test_triage_api_returns_overdue_and_due_today(self) -> None:
    response = self.client.get("/api/triage")
    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertIn("overdue_groups", payload)
    self.assertIn("due_today", payload)

  def test_my_tasks_api_reports_unlinked_email(self) -> None:
    response = self.client.get("/api/my-tasks")
    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertFalse(payload["linked"])
    self.assertEqual(payload["tasks"], [])

  def test_my_tasks_api_returns_linked_member_tasks(self) -> None:
    self.repository.members[0] = TeamMember(name="Rahul", number="919000000001", email="ceo@example.com")
    response = self.client.get("/api/my-tasks")
    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertTrue(payload["linked"])
    self.assertEqual(len(payload["tasks"]), 1)

  def test_reports_api_returns_kpis(self) -> None:
    response = self.client.get("/api/reports")
    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertEqual(payload["total_tasks"], 1)
    self.assertEqual(len(payload["daily_trend"]), 14)

  def test_send_api_logs_message_for_chat_history(self) -> None:
    response = self.client.post(
      "/api/send",
      json={"to": "919000000001", "message": "Manual follow-up"},
    )

    self.assertEqual(response.status_code, 200)
    chat_response = self.client.get("/api/chats")
    self.assertEqual(chat_response.status_code, 200)
    payload = chat_response.json()
    messages = payload["conversations"][0]["messages"]
    self.assertEqual(messages[-1]["text"], "Manual follow-up")
    self.assertEqual(messages[-1]["direction"], "out")


if __name__ == "__main__":
  unittest.main()
