from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from ceod.app import create_app
from ceod.models import (
  AllTask,
  DOER_TASK_STATUS_COMPLETED,
  DepartmentTaskAssignment,
  DoerTaskCompletion,
  DoerTaskUpdateEntry,
  EmployeeRecord,
  LoginResult,
  ReportingRelation,
  TaskPriority,
  TaskRevision,
  UserAccount,
  UserRole,
)
from ceod.service import DepartmentTaskService


class FakeDepartmentRepository:
  def __init__(self) -> None:
    self.employees: dict[str, EmployeeRecord] = {}
    self.users: dict[str, UserAccount] = {}
    self.reporting: list[ReportingRelation] = []
    self.all_tasks: dict[str, AllTask] = {}
    self.department_tasks: list[DepartmentTaskAssignment] = []
    self.revisions: list[TaskRevision] = []
    self.completions: list[DoerTaskCompletion] = []
    self.updates: list[DoerTaskUpdateEntry] = []
    self._counter = 0

  def _next_id(self, prefix: str) -> str:
    self._counter += 1
    return f"{prefix}{self._counter}"

  # -- employee / users ----------------------------------------------------

  def get_employee_by_email(self, email: str) -> EmployeeRecord | None:
    return self.employees.get(email.strip().lower())

  def get_user(self, email: str) -> UserAccount | None:
    return self.users.get(email.strip().lower())

  def list_users(self) -> list[UserAccount]:
    return sorted(self.users.values(), key=lambda u: u.name)

  def upsert_user(self, *, user_id: str, name: str, role: UserRole, active: bool = True) -> UserAccount:
    account = UserAccount(user_id=user_id, name=name, role=role, active=active)
    self.users[user_id] = account
    return account

  def deactivate_user(self, email: str) -> None:
    existing = self.users.get(email)
    if existing:
      self.users[email] = replace(existing, active=False)

  # -- reporting -------------------------------------------------------------

  def list_reporting_relations(self) -> list[ReportingRelation]:
    return list(self.reporting)

  def get_team_for(self, email: str) -> list[ReportingRelation]:
    clean_email = email.strip().lower()
    return [r for r in self.reporting if r.tl_email == clean_email or r.manager_email == clean_email]

  def add_reporting_relation(
    self, *, tl_name, manager_name, emp_name, emp_id, tl_email=None, manager_email=None
  ) -> ReportingRelation:
    relation = ReportingRelation(
      tl_name=tl_name, manager_name=manager_name, emp_name=emp_name, emp_id=emp_id,
      tl_email=tl_email, manager_email=manager_email,
    )
    self.reporting.append(relation)
    return relation

  # -- All_Tasks ---------------------------------------------------------------

  def list_all_tasks(self) -> list[AllTask]:
    return sorted(self.all_tasks.values(), key=lambda t: t.created_date_time, reverse=True)

  def list_all_tasks_for_department(self, department: str) -> list[AllTask]:
    return [t for t in self.all_tasks.values() if t.assigned_department == department]

  def get_all_task(self, task_id: str) -> AllTask | None:
    return self.all_tasks.get(task_id)

  # -- Department_Tasks --------------------------------------------------------

  def _current_rows_by_task(self) -> dict[str, DepartmentTaskAssignment]:
    current: dict[str, DepartmentTaskAssignment] = {}
    for row in self.department_tasks:
      current[row.task_id] = row
    return current

  def get_current_department_assignment(self, task_id: str) -> DepartmentTaskAssignment | None:
    rows = [r for r in self.department_tasks if r.task_id == task_id]
    return rows[-1] if rows else None

  def list_current_department_assignments(self) -> dict[str, DepartmentTaskAssignment]:
    return self._current_rows_by_task()

  def list_current_department_assignments_for_department(self, department: str) -> dict[str, DepartmentTaskAssignment]:
    current = self._current_rows_by_task()
    return {
      task_id: assignment
      for task_id, assignment in current.items()
      if self.all_tasks.get(task_id) and self.all_tasks[task_id].assigned_department == department
    }

  def list_current_assignments_for_doer(self, doer_email: str) -> list[DepartmentTaskAssignment]:
    current = self._current_rows_by_task()
    return [a for a in current.values() if a.assigned_to_doer == doer_email.strip().lower()]

  def assign_department_task(
    self, *, task_id, task_brief, comments, department_due_date, priority, assigned_by, assigned_to_doer
  ) -> DepartmentTaskAssignment:
    doer_task_id = self._next_id("DT")
    assignment = DepartmentTaskAssignment(
      task_id=task_id,
      task_brief=task_brief,
      comments=comments,
      department_due_date=department_due_date,
      priority=priority,
      assigned_by=assigned_by.strip().lower(),
      assigned_to_doer=assigned_to_doer.strip().lower(),
      doer_task_id=doer_task_id,
      row_created_at=f"T{len(self.department_tasks)}",
    )
    self.department_tasks.append(assignment)
    self.add_doer_task_update(
      task_id=task_id, doer_task_id=doer_task_id, task_update="In Progress", comments=None, submitted_by=assigned_by
    )
    return assignment

  # -- Task_Revised --------------------------------------------------------------

  def revise_due_date(self, *, task_id: str, revised_date: str, revised_by: str) -> TaskRevision:
    revision = TaskRevision(
      task_id=task_id,
      revised_id=self._next_id("R"),
      revised_date=revised_date,
      revised_by=revised_by.strip().lower(),
      revised_at=f"T{len(self.revisions)}",
    )
    self.revisions.append(revision)
    return revision

  def list_revisions_for_task(self, task_id: str) -> list[TaskRevision]:
    return [r for r in self.revisions if r.task_id == task_id]

  def list_revisions_for_tasks(self, task_ids: list[str]) -> dict[str, list[TaskRevision]]:
    result: dict[str, list[TaskRevision]] = {}
    for r in self.revisions:
      if r.task_id in task_ids:
        result.setdefault(r.task_id, []).append(r)
    return result

  # -- doer_Tasks / doer_Task_update --------------------------------------------

  def mark_doer_task_complete(
    self, *, task_id, doer_task_id, task_brief, assigned_by, submitted_by, comments, completion_date
  ) -> DoerTaskCompletion:
    completion = DoerTaskCompletion(
      task_id=task_id,
      task_brief=task_brief,
      task_status=DOER_TASK_STATUS_COMPLETED,
      completion_date=completion_date,
      comments=comments,
      assigned_by=assigned_by.strip().lower(),
      doer_task_id=doer_task_id,
      submitted_by=submitted_by.strip().lower(),
    )
    self.completions.append(completion)
    return completion

  def get_completion_for_doer_task(self, doer_task_id: str) -> DoerTaskCompletion | None:
    matches = [c for c in self.completions if c.doer_task_id == doer_task_id]
    return matches[-1] if matches else None

  def get_completions_for_doer_tasks(self, doer_task_ids: list[str]) -> dict[str, DoerTaskCompletion]:
    return {c.doer_task_id: c for c in self.completions if c.doer_task_id in doer_task_ids}

  def add_doer_task_update(
    self, *, task_id: str, doer_task_id: str, task_update: str, comments: str | None, submitted_by: str
  ) -> DoerTaskUpdateEntry:
    entry = DoerTaskUpdateEntry(
      task_id=task_id,
      task_update_id=self._next_id("U"),
      doer_task_id=doer_task_id,
      task_update=task_update,
      comments=comments,
      submitted_by=submitted_by.strip().lower(),
      row_created_at=f"T{len(self.updates)}",
    )
    self.updates.append(entry)
    return entry

  def list_updates_for_doer_task(self, doer_task_id: str) -> list[DoerTaskUpdateEntry]:
    return [u for u in self.updates if u.doer_task_id == doer_task_id]

  def get_latest_updates_for_doer_tasks(self, doer_task_ids: list[str]) -> dict[str, DoerTaskUpdateEntry]:
    result: dict[str, DoerTaskUpdateEntry] = {}
    for u in self.updates:
      if u.doer_task_id in doer_task_ids:
        result[u.doer_task_id] = u
    return result


def make_task(task_id="TASK1", department="Sales", priority=TaskPriority.MEDIUM) -> AllTask:
  return AllTask(
    task_id=task_id,
    created_date_time="2026-01-01T00:00:00Z",
    task_brief="Follow up with client",
    assigned_department=department,
    ceo_task_due_date="2026-01-10",
    priority=priority,
    ny_task_id=None,
  )


class DepartmentTaskServiceTests(unittest.TestCase):
  def setUp(self) -> None:
    self.repo = FakeDepartmentRepository()
    self.service = DepartmentTaskService(repository=self.repo, timezone_name="Asia/Kolkata")

    self.repo.employees["tl@example.com"] = EmployeeRecord(
      work_email="tl@example.com", full_name="Priya TL", employee_number="E1", department="Sales", job_title="Team Lead"
    )
    self.repo.employees["doer@example.com"] = EmployeeRecord(
      work_email="doer@example.com", full_name="Rahul Doer", employee_number="E2", department="Sales", job_title="Executive"
    )
    self.repo.employees["outsider@example.com"] = EmployeeRecord(
      work_email="outsider@example.com", full_name="No Access", employee_number="E3", department="Sales", job_title="Executive"
    )
    self.repo.users["tl@example.com"] = UserAccount(user_id="tl@example.com", name="Priya TL", role=UserRole.TL, active=True)
    self.repo.reporting.append(
      ReportingRelation(
        tl_name="Priya TL", manager_name=None, emp_name="Rahul Doer", emp_id="doer@example.com",
        tl_email="tl@example.com",
      )
    )
    self.repo.all_tasks["TASK1"] = make_task()

    self.tl_login = LoginResult(email="tl@example.com", name="Priya TL", role=UserRole.TL, department="Sales")
    self.doer_login = LoginResult(email="doer@example.com", name="Rahul Doer", role=UserRole.MEMBER, department="Sales")

  # -- login -----------------------------------------------------------------

  def test_resolve_login_returns_role_from_users_table(self) -> None:
    login = self.service.resolve_login("tl@example.com")
    self.assertIsNotNone(login)
    self.assertEqual(login.role, UserRole.TL)
    self.assertEqual(login.department, "Sales")
    self.assertTrue(login.is_management)

  def test_resolve_login_defaults_to_member_when_not_in_users_table(self) -> None:
    login = self.service.resolve_login("doer@example.com")
    self.assertEqual(login.role, UserRole.MEMBER)
    self.assertFalse(login.is_management)

  def test_resolve_login_rejects_unknown_employee(self) -> None:
    self.assertIsNone(self.service.resolve_login("nobody@example.com"))

  # -- department queue / assignment ------------------------------------------

  def test_get_department_queue_filters_by_department_for_tl(self) -> None:
    self.repo.all_tasks["OTHER"] = make_task(task_id="OTHER", department="Finance")
    items = self.service.get_department_queue(self.tl_login)
    self.assertEqual([item.task.task_id for item in items], ["TASK1"])

  def test_admin_sees_all_departments(self) -> None:
    self.repo.all_tasks["OTHER"] = make_task(task_id="OTHER", department="Finance")
    admin_login = LoginResult(email="admin@example.com", name="Admin", role=UserRole.ADMIN, department=None)
    items = self.service.get_department_queue(admin_login)
    self.assertEqual({item.task.task_id for item in items}, {"TASK1", "OTHER"})

  def test_admin_sees_all_reporting_relations_as_team(self) -> None:
    admin_login = LoginResult(email="admin@example.com", name="Admin", role=UserRole.ADMIN, department=None)
    team = self.service.get_team(admin_login)
    self.assertEqual([member.emp_id for member in team], ["doer@example.com"])

  def test_assign_task_mints_doer_task_id_and_first_update(self) -> None:
    assignment = self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="please handle", due_date="2026-01-15", priority=TaskPriority.HIGH,
    )
    self.assertEqual(assignment.assigned_to_doer, "doer@example.com")
    updates = self.repo.list_updates_for_doer_task(assignment.doer_task_id)
    self.assertEqual(len(updates), 1)
    self.assertEqual(updates[0].task_update, "In Progress")
    self.assertEqual(updates[0].submitted_by, "tl@example.com")

  def test_assign_task_rejects_non_management(self) -> None:
    with self.assertRaises(ValueError):
      self.service.assign_task(
        login=self.doer_login, task_id="TASK1", assigned_to_doer="doer@example.com",
        comments="", due_date=None, priority=TaskPriority.MEDIUM,
      )

  def test_assign_task_rejects_doer_not_on_team(self) -> None:
    with self.assertRaises(ValueError):
      self.service.assign_task(
        login=self.tl_login, task_id="TASK1", assigned_to_doer="outsider@example.com",
        comments="", due_date=None, priority=TaskPriority.MEDIUM,
      )

  def test_assign_task_unknown_task_id_raises(self) -> None:
    with self.assertRaises(ValueError):
      self.service.assign_task(
        login=self.tl_login, task_id="NOPE", assigned_to_doer="doer@example.com",
        comments="", due_date=None, priority=TaskPriority.MEDIUM,
      )

  def test_reassign_keeps_old_row_as_history(self) -> None:
    first = self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="", due_date=None, priority=TaskPriority.MEDIUM,
    )
    self.repo.reporting.append(
      ReportingRelation(
        tl_name="Priya TL", manager_name=None, emp_name="Second Doer", emp_id="doer2@example.com",
        tl_email="tl@example.com",
      )
    )
    second = self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer2@example.com",
      comments="", due_date=None, priority=TaskPriority.MEDIUM,
    )
    self.assertNotEqual(first.doer_task_id, second.doer_task_id)
    self.assertEqual(len(self.repo.department_tasks), 2)
    current = self.repo.get_current_department_assignment("TASK1")
    self.assertEqual(current.assigned_to_doer, "doer2@example.com")

  # -- revise ------------------------------------------------------------------

  def test_revise_due_date_requires_existing_assignment(self) -> None:
    with self.assertRaises(ValueError):
      self.service.revise_due_date(login=self.tl_login, task_id="TASK1", revised_date="2026-01-20")

  def test_revise_due_date_happy_path(self) -> None:
    self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="", due_date="2026-01-10", priority=TaskPriority.MEDIUM,
    )
    revision = self.service.revise_due_date(login=self.tl_login, task_id="TASK1", revised_date="2026-01-20")
    self.assertEqual(revision.revised_date, "2026-01-20")
    self.assertEqual(revision.revised_by, "tl@example.com")

  # -- doer view / completion / updates -----------------------------------------

  def test_get_my_doer_view_includes_effective_due_date(self) -> None:
    self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="", due_date="2026-01-10", priority=TaskPriority.MEDIUM,
    )
    views = self.service.get_my_doer_view(self.doer_login)
    self.assertEqual(len(views), 1)
    self.assertEqual(views[0].effective_due_date, "2026-01-10")
    self.assertFalse(views[0].is_completed)

  def test_effective_due_date_prefers_latest_revision(self) -> None:
    self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="", due_date="2026-01-10", priority=TaskPriority.MEDIUM,
    )
    self.service.revise_due_date(login=self.tl_login, task_id="TASK1", revised_date="2026-01-20")
    views = self.service.get_my_doer_view(self.doer_login)
    self.assertEqual(views[0].effective_due_date, "2026-01-20")

  def test_submit_completion_happy_path(self) -> None:
    assignment = self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="", due_date=None, priority=TaskPriority.MEDIUM,
    )
    completion = self.service.submit_completion(
      login=self.doer_login, task_id="TASK1", doer_task_id=assignment.doer_task_id, comments="done"
    )
    self.assertEqual(completion.task_status, DOER_TASK_STATUS_COMPLETED)
    views = self.service.get_my_doer_view(self.doer_login)
    self.assertTrue(views[0].is_completed)

  def test_submit_completion_rejects_wrong_doer(self) -> None:
    assignment = self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="", due_date=None, priority=TaskPriority.MEDIUM,
    )
    other_login = LoginResult(email="outsider@example.com", name="No Access", role=UserRole.MEMBER, department="Sales")
    with self.assertRaises(ValueError):
      self.service.submit_completion(
        login=other_login, task_id="TASK1", doer_task_id=assignment.doer_task_id, comments=""
      )

  def test_submit_completion_rejects_double_complete(self) -> None:
    assignment = self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="", due_date=None, priority=TaskPriority.MEDIUM,
    )
    self.service.submit_completion(login=self.doer_login, task_id="TASK1", doer_task_id=assignment.doer_task_id)
    with self.assertRaises(ValueError):
      self.service.submit_completion(login=self.doer_login, task_id="TASK1", doer_task_id=assignment.doer_task_id)

  def test_submit_update_allows_doer_and_assigning_tl(self) -> None:
    assignment = self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="", due_date=None, priority=TaskPriority.MEDIUM,
    )
    self.service.submit_update(
      login=self.doer_login, task_id="TASK1", doer_task_id=assignment.doer_task_id,
      task_update="In Progress", comments="working on it",
    )
    entry = self.service.submit_update(
      login=self.tl_login, task_id="TASK1", doer_task_id=assignment.doer_task_id,
      task_update="Abandon", comments="doer left the company",
    )
    self.assertEqual(entry.task_update, "Abandon")

  def test_submit_update_rejects_unrelated_person(self) -> None:
    assignment = self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="", due_date=None, priority=TaskPriority.MEDIUM,
    )
    other_login = LoginResult(email="outsider@example.com", name="No Access", role=UserRole.MEMBER, department="Sales")
    with self.assertRaises(ValueError):
      self.service.submit_update(
        login=other_login, task_id="TASK1", doer_task_id=assignment.doer_task_id,
        task_update="In Progress", comments="",
      )

  def test_submit_update_rejects_invalid_status(self) -> None:
    assignment = self.service.assign_task(
      login=self.tl_login, task_id="TASK1", assigned_to_doer="doer@example.com",
      comments="", due_date=None, priority=TaskPriority.MEDIUM,
    )
    with self.assertRaises(ValueError):
      self.service.submit_update(
        login=self.doer_login, task_id="TASK1", doer_task_id=assignment.doer_task_id,
        task_update="Not A Status", comments="",
      )

  # -- admin ---------------------------------------------------------------

  def test_upsert_user_validates_email_and_name(self) -> None:
    with self.assertRaises(ValueError):
      self.service.upsert_user(user_id="not-an-email", name="X", role=UserRole.MEMBER)
    with self.assertRaises(ValueError):
      self.service.upsert_user(user_id="a@example.com", name="  ", role=UserRole.MEMBER)

  def test_add_reporting_relation_requires_tl_or_manager(self) -> None:
    with self.assertRaises(ValueError):
      self.service.add_reporting_relation(
        tl_name=None, manager_name=None, emp_name="X", emp_id="x@example.com", tl_email="tl@example.com"
      )

  def test_add_reporting_relation_requires_an_email(self) -> None:
    with self.assertRaises(ValueError):
      self.service.add_reporting_relation(
        tl_name="Priya TL", manager_name=None, emp_name="X", emp_id="x@example.com"
      )

  def test_add_reporting_relation_happy_path(self) -> None:
    relation = self.service.add_reporting_relation(
      tl_name="Priya TL", manager_name=None, emp_name="New Doer", emp_id="NEW@Example.com",
      tl_email="TL@Example.com",
    )
    self.assertEqual(relation.emp_id, "new@example.com")
    self.assertEqual(relation.tl_email, "tl@example.com")
    self.assertIn(relation, self.repo.reporting)

  def test_get_team_matches_by_email_not_name(self) -> None:
    # A second TL who happens to share Priya TL's display name must not see
    # her team — matching is strictly on TL_Email/Manager_Email.
    impostor_login = LoginResult(email="impostor@example.com", name="Priya TL", role=UserRole.TL, department="Sales")
    self.assertEqual(self.service.get_team(impostor_login), [])
    self.assertEqual([m.emp_id for m in self.service.get_team(self.tl_login)], ["doer@example.com"])


class DepartmentAppRouteTests(unittest.TestCase):
  def setUp(self) -> None:
    self.repo = FakeDepartmentRepository()
    self.department_service = DepartmentTaskService(repository=self.repo, timezone_name="Asia/Kolkata")

    self.repo.employees["tl@example.com"] = EmployeeRecord(
      work_email="tl@example.com", full_name="Priya TL", employee_number="E1", department="Sales", job_title="Team Lead"
    )
    self.repo.employees["doer@example.com"] = EmployeeRecord(
      work_email="doer@example.com", full_name="Rahul Doer", employee_number="E2", department="Sales", job_title="Executive"
    )
    self.repo.users["tl@example.com"] = UserAccount(user_id="tl@example.com", name="Priya TL", role=UserRole.TL, active=True)
    self.repo.users["admin@example.com"] = UserAccount(user_id="admin@example.com", name="Admin", role=UserRole.ADMIN, active=True)
    self.repo.reporting.append(
      ReportingRelation(
        tl_name="Priya TL", manager_name=None, emp_name="Rahul Doer", emp_id="doer@example.com",
        tl_email="tl@example.com",
      )
    )
    self.repo.all_tasks["TASK1"] = make_task()

    self.session_secret = "test-session-secret"
    fake_container = SimpleNamespace(
      settings=SimpleNamespace(
        script_timezone="Asia/Kolkata",
        dashboard_auth_enabled=True,
        google_oauth_client_id=None,
        google_oauth_client_secret=None,
        session_secret=self.session_secret,
        public_base_url=None,
        cron_secret="test-cron-secret",
      ),
      service=SimpleNamespace(get_chat_log=lambda: []),
      department_service=self.department_service,
      whatsapp=SimpleNamespace(),
      repository=self.repo,
      close=lambda: None,
    )
    self.client = TestClient(create_app(container=fake_container))

  def _login_as(self, client: TestClient, email: str, name: str, role: UserRole, department: str | None) -> None:
    payload = {"email": email, "name": name, "role": role.value, "department": department}
    token = URLSafeTimedSerializer(self.session_secret, salt="ceod-session").dumps(payload)
    client.cookies.set("ceod_session", token)

  def test_root_redirects_unauthenticated_to_login(self) -> None:
    anon = TestClient(self.client.app, follow_redirects=False)
    response = anon.get("/")
    self.assertEqual(response.status_code, 302)
    self.assertTrue(response.headers["location"].startswith("/login"))

  def test_root_redirects_tl_to_department_tasks(self) -> None:
    client = TestClient(self.client.app, follow_redirects=False)
    self._login_as(client, "tl@example.com", "Priya TL", UserRole.TL, "Sales")
    response = client.get("/")
    self.assertEqual(response.status_code, 302)
    self.assertEqual(response.headers["location"], "/department-tasks")

  def test_root_redirects_member_to_my_tasks(self) -> None:
    client = TestClient(self.client.app, follow_redirects=False)
    self._login_as(client, "doer@example.com", "Rahul Doer", UserRole.MEMBER, "Sales")
    response = client.get("/")
    self.assertEqual(response.status_code, 302)
    self.assertEqual(response.headers["location"], "/my-tasks")

  def test_member_cannot_load_department_tasks_page(self) -> None:
    client = TestClient(self.client.app, follow_redirects=False)
    self._login_as(client, "doer@example.com", "Rahul Doer", UserRole.MEMBER, "Sales")
    response = client.get("/department-tasks")
    self.assertEqual(response.status_code, 302)
    self.assertEqual(response.headers["location"], "/my-tasks")

  def test_non_admin_cannot_load_admin_page(self) -> None:
    client = TestClient(self.client.app, follow_redirects=False)
    self._login_as(client, "tl@example.com", "Priya TL", UserRole.TL, "Sales")
    response = client.get("/admin")
    self.assertEqual(response.status_code, 302)
    self.assertEqual(response.headers["location"], "/")

  def test_admin_page_loads_for_admin(self) -> None:
    client = TestClient(self.client.app)
    self._login_as(client, "admin@example.com", "Admin", UserRole.ADMIN, None)
    response = client.get("/admin")
    self.assertEqual(response.status_code, 200)
    self.assertIn("Users", response.text)

  def test_full_assign_and_complete_flow_via_api(self) -> None:
    tl_client = TestClient(self.client.app)
    self._login_as(tl_client, "tl@example.com", "Priya TL", UserRole.TL, "Sales")

    queue = tl_client.get("/api/department-queue").json()
    self.assertEqual(len(queue["items"]), 1)
    self.assertIsNone(queue["items"][0]["current_assignment"])

    team = tl_client.get("/api/team").json()
    self.assertEqual(team["team"][0]["emp_id"], "doer@example.com")

    assign_response = tl_client.post(
      "/api/department-tasks/TASK1/assign",
      json={"assigned_to_doer": "doer@example.com", "comments": "please handle", "due_date": "2026-01-15", "priority": "High"},
    )
    self.assertEqual(assign_response.status_code, 201)
    doer_task_id = assign_response.json()["assignment"]["doer_task_id"]

    doer_client = TestClient(self.client.app)
    self._login_as(doer_client, "doer@example.com", "Rahul Doer", UserRole.MEMBER, "Sales")

    my_tasks = doer_client.get("/api/my-doer-tasks").json()
    self.assertEqual(len(my_tasks["tasks"]), 1)
    self.assertEqual(my_tasks["tasks"][0]["assignment"]["doer_task_id"], doer_task_id)

    complete_response = doer_client.post(
      f"/api/doer-tasks/TASK1/complete", json={"doer_task_id": doer_task_id, "comments": "all done"}
    )
    self.assertEqual(complete_response.status_code, 201)

    my_tasks_after = doer_client.get("/api/my-doer-tasks").json()
    self.assertTrue(my_tasks_after["tasks"][0]["is_completed"])

  def test_assign_requires_management_role(self) -> None:
    doer_client = TestClient(self.client.app)
    self._login_as(doer_client, "doer@example.com", "Rahul Doer", UserRole.MEMBER, "Sales")
    response = doer_client.post(
      "/api/department-tasks/TASK1/assign",
      json={"assigned_to_doer": "doer@example.com", "comments": "", "due_date": None, "priority": "Medium"},
    )
    self.assertEqual(response.status_code, 403)

  def test_admin_api_requires_admin_role(self) -> None:
    tl_client = TestClient(self.client.app)
    self._login_as(tl_client, "tl@example.com", "Priya TL", UserRole.TL, "Sales")
    response = tl_client.get("/api/admin/users")
    self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
  unittest.main()
