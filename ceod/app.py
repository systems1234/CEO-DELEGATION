from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from ceod.config import Settings
from ceod.models import LoginResult, TaskPriority, UserRole

if TYPE_CHECKING:
  from ceod.container import AppContainer

LOGGER = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).resolve().parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))

# Everything not listed here requires a valid dashboard session when
# dashboard_auth_enabled is true (denylist of public paths, rather than an
# allowlist of protected ones, since the route surface keeps growing).
PUBLIC_PATHS = frozenset({"/login", "/auth/login", "/auth/callback", "/logout", "/healthz", "/webhook"})
PUBLIC_PREFIXES = ("/static/",)

SESSION_COOKIE = "ceod_session"
SESSION_MAX_AGE = 60 * 60 * 24  # 24 hours


class SendTextPayload(BaseModel):
  to: str = Field(min_length=1)
  message: str = Field(min_length=1)


class AssignTaskPayload(BaseModel):
  assigned_to_doer: str = Field(min_length=1)
  comments: str = ""
  due_date: str | None = None
  priority: str = "Medium"


class ReviseDatePayload(BaseModel):
  revised_date: str = Field(min_length=1)


class DoerCompletePayload(BaseModel):
  doer_task_id: str = Field(min_length=1)
  comments: str = ""


class DoerUpdatePayload(BaseModel):
  doer_task_id: str = Field(min_length=1)
  task_update: str = Field(min_length=1)
  comments: str = ""


class UpsertUserPayload(BaseModel):
  email: str = Field(min_length=1)
  name: str = Field(min_length=1)
  role: str = Field(min_length=1)
  active: bool = True


class ReportingRelationPayload(BaseModel):
  tl_name: str | None = None
  manager_name: str | None = None
  emp_name: str = Field(min_length=1)
  emp_id: str = Field(min_length=1)
  tl_email: str | None = None
  manager_email: str | None = None


def create_app(settings: Settings | None = None, container: "AppContainer" | None = None) -> FastAPI:
  logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    force=True,
  )
  logging.getLogger("ceod").setLevel(logging.DEBUG)
  LOGGER.info("Logging initialised — level=DEBUG")

  resolved_container = container or _build_runtime_container(settings)
  oauth = _build_oauth_client(resolved_container.settings)

  @asynccontextmanager
  async def lifespan(app: FastAPI):
    app.state.container = resolved_container
    try:
      yield
    finally:
      app.state.container.close()

  app = FastAPI(title="CEO Delegation Service", version="2.0.0", lifespan=lifespan)
  app.state.container = resolved_container
  app.state.oauth = oauth
  app.add_middleware(
    SessionMiddleware,
    secret_key=resolved_container.settings.session_secret or "ceod-fallback-secret-change-me",
    session_cookie="ceod_oauth_state",
    max_age=600,
  )
  app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")

  @app.middleware("http")
  async def protect_dashboard_routes(request: Request, call_next):
    if not _is_public_path(request.url.path):
      settings = app.state.container.settings
      if getattr(settings, "dashboard_auth_enabled", False):
        if _current_login(request, settings) is None:
          next_url = request.url.path
          return RedirectResponse(url=f"/login?next={next_url}", status_code=302)

    response = await call_next(request)
    if not _is_public_path(request.url.path):
      response.headers["Cache-Control"] = "no-store"
    return response

  # -- auth -----------------------------------------------------------------

  @app.get("/login", response_class=HTMLResponse)
  def login_page(request: Request, next: str = "/", error: str = "") -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
      request=request, name="login.html", context={"next": next, "error": error},
    )

  @app.get("/auth/login")
  async def auth_login(request: Request, next: str = "/") -> Response:
    settings = app.state.container.settings
    request.session["ceod_next"] = next if next.startswith("/") else "/"
    redirect_uri = _oauth_redirect_uri(request, settings)
    return await app.state.oauth.google.authorize_redirect(request, redirect_uri)

  @app.get("/auth/callback")
  async def auth_callback(request: Request) -> Response:
    settings = app.state.container.settings
    next_url = request.session.pop("ceod_next", "/")
    try:
      token = await app.state.oauth.google.authorize_access_token(request)
      userinfo = token.get("userinfo") or {}
    except Exception:
      LOGGER.exception("Google OAuth callback failed")
      return RedirectResponse(url="/login?error=Sign-in+failed.+Please+try+again.", status_code=302)

    email = str(userinfo.get("email", "")).strip().lower()
    email_verified = bool(userinfo.get("email_verified", False))
    if not email or not email_verified:
      LOGGER.warning("Rejected Google sign-in — unverified or missing email")
      return RedirectResponse(url="/login?error=Your+Google+account+could+not+be+verified.", status_code=302)

    login = app.state.container.department_service.resolve_login(email)
    if login is None:
      LOGGER.warning("Rejected sign-in for email=%s (no ceo_del_sys access on employee record)", email)
      return RedirectResponse(
        url="/login?error=Your+account+does+not+have+access+to+this+system.", status_code=302
      )

    token_value = _make_session_token(login, settings)
    destination = next_url if next_url.startswith("/") else "/"
    response = RedirectResponse(url=destination, status_code=302)
    response.set_cookie(
      SESSION_COOKIE, token_value,
      max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=True,
    )
    return response

  @app.get("/logout")
  def logout() -> Response:
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response

  @app.get("/healthz")
  def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})

  # -- pages ------------------------------------------------------------------

  @app.get("/")
  def root(request: Request) -> Response:
    login = _current_login(request, app.state.container.settings)
    if login is None:
      return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/department-tasks" if login.is_management else "/my-tasks", status_code=302)

  @app.get("/department-tasks", response_class=HTMLResponse)
  def department_tasks_page(request: Request) -> Response:
    login = _current_login(request, app.state.container.settings)
    if login is None:
      return RedirectResponse(url="/login", status_code=302)
    if not login.is_management:
      return RedirectResponse(url="/my-tasks", status_code=302)
    return TEMPLATES.TemplateResponse(
      request=request,
      name="department_tasks.html",
      context={
        "app_title": "CEO Mission Control - Department Tasks",
        "active_page": "department-tasks",
        "login": login,
        "is_admin": login.role == UserRole.ADMIN,
      },
    )

  @app.get("/my-tasks", response_class=HTMLResponse)
  def my_tasks_page(request: Request) -> Response:
    login = _current_login(request, app.state.container.settings)
    if login is None:
      return RedirectResponse(url="/login", status_code=302)
    return TEMPLATES.TemplateResponse(
      request=request,
      name="my_tasks.html",
      context={
        "app_title": "CEO Mission Control - My Tasks",
        "active_page": "my-tasks",
        "login": login,
        "is_admin": login.role == UserRole.ADMIN,
      },
    )

  @app.get("/admin", response_class=HTMLResponse)
  def admin_page(request: Request) -> Response:
    login = _current_login(request, app.state.container.settings)
    if login is None:
      return RedirectResponse(url="/login", status_code=302)
    if login.role != UserRole.ADMIN:
      return RedirectResponse(url="/", status_code=302)
    return TEMPLATES.TemplateResponse(
      request=request,
      name="admin.html",
      context={
        "app_title": "CEO Mission Control - Admin",
        "active_page": "admin",
        "login": login,
        "is_admin": True,
        "roles": [role.value for role in UserRole],
      },
    )

  @app.get("/chat", response_class=HTMLResponse)
  def chat_page(request: Request) -> Response:
    login = _current_login(request, app.state.container.settings)
    if login is None:
      return RedirectResponse(url="/login", status_code=302)
    return TEMPLATES.TemplateResponse(
      request=request,
      name="chat.html",
      context={"app_title": "CEO Mission Control - Chats", "login": login, "is_admin": login.role == UserRole.ADMIN},
    )

  # -- department-task API (TL / Manager / Senior / Admin) ---------------------

  @app.get("/api/department-queue")
  def department_queue_api(request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    _require_management(login)
    items = app.state.container.department_service.get_department_queue(login)
    return JSONResponse({"items": [_serialise(item) for item in items]})

  @app.get("/api/team")
  def team_api(request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    _require_management(login)
    team = app.state.container.department_service.get_team(login)
    return JSONResponse({"team": [_serialise(member) for member in team]})

  @app.post("/api/department-tasks/{task_id}/assign")
  def assign_department_task_api(task_id: str, payload: AssignTaskPayload, request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    _require_management(login)
    try:
      priority = TaskPriority(payload.priority)
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=f'Invalid priority "{payload.priority}"') from exc
    try:
      assignment = app.state.container.department_service.assign_task(
        login=login,
        task_id=task_id,
        assigned_to_doer=payload.assigned_to_doer,
        comments=payload.comments,
        due_date=payload.due_date,
        priority=priority,
      )
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"assignment": _serialise(assignment)}, status_code=201)

  @app.post("/api/department-tasks/{task_id}/revise")
  def revise_due_date_api(task_id: str, payload: ReviseDatePayload, request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    _require_management(login)
    try:
      revision = app.state.container.department_service.revise_due_date(
        login=login, task_id=task_id, revised_date=payload.revised_date,
      )
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"revision": _serialise(revision)}, status_code=201)

  # -- doer API (Member) --------------------------------------------------------

  @app.get("/api/my-doer-tasks")
  def my_doer_tasks_api(request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    views = app.state.container.department_service.get_my_doer_view(login)
    return JSONResponse({"tasks": [_serialise_doer_view(view) for view in views]})

  @app.post("/api/doer-tasks/{task_id}/complete")
  def complete_doer_task_api(task_id: str, payload: DoerCompletePayload, request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    try:
      completion = app.state.container.department_service.submit_completion(
        login=login, task_id=task_id, doer_task_id=payload.doer_task_id, comments=payload.comments,
      )
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"completion": _serialise(completion)}, status_code=201)

  @app.post("/api/doer-tasks/{task_id}/update")
  def update_doer_task_api(task_id: str, payload: DoerUpdatePayload, request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    try:
      entry = app.state.container.department_service.submit_update(
        login=login,
        task_id=task_id,
        doer_task_id=payload.doer_task_id,
        task_update=payload.task_update,
        comments=payload.comments,
      )
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"update": _serialise(entry)}, status_code=201)

  # -- admin API ----------------------------------------------------------------

  @app.get("/api/admin/users")
  def list_users_api(request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    _require_admin(login)
    users = app.state.container.department_service.list_users()
    return JSONResponse({"users": [_serialise(user) for user in users]})

  @app.post("/api/admin/users", status_code=201)
  def upsert_user_api(payload: UpsertUserPayload, request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    _require_admin(login)
    try:
      role = UserRole(payload.role)
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=f'Invalid role "{payload.role}"') from exc
    try:
      user = app.state.container.department_service.upsert_user(
        user_id=payload.email, name=payload.name, role=role, active=payload.active,
      )
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"user": _serialise(user)}, status_code=201)

  @app.post("/api/admin/users/{email}/deactivate")
  def deactivate_user_api(email: str, request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    _require_admin(login)
    app.state.container.department_service.deactivate_user(email)
    return JSONResponse({"ok": True})

  @app.get("/api/admin/reporting")
  def list_reporting_api(request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    _require_admin(login)
    relations = app.state.container.department_service.list_reporting_relations()
    return JSONResponse({"relations": [_serialise(r) for r in relations]})

  @app.post("/api/admin/reporting", status_code=201)
  def add_reporting_api(payload: ReportingRelationPayload, request: Request) -> JSONResponse:
    login = _require_login(request, app.state.container.settings)
    _require_admin(login)
    try:
      relation = app.state.container.department_service.add_reporting_relation(
        tl_name=payload.tl_name, manager_name=payload.manager_name,
        emp_name=payload.emp_name, emp_id=payload.emp_id,
        tl_email=payload.tl_email, manager_email=payload.manager_email,
      )
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"relation": _serialise(relation)}, status_code=201)

  # -- chat viewer (unrelated to the task schema, kept as-is) -------------------

  @app.get("/api/chats")
  def get_chats(request: Request) -> JSONResponse:
    _require_login(request, app.state.container.settings)
    log = app.state.container.service.get_chat_log()
    conversations: dict[str, dict] = {}
    for entry in log:
      num = entry["number"]
      if num not in conversations:
        conversations[num] = {"number": num, "name": entry["name"], "messages": [], "last_timestamp": ""}
      conversations[num]["messages"].append({"timestamp": entry["timestamp"], "direction": entry["direction"], "text": entry["text"]})
      conversations[num]["last_timestamp"] = entry["timestamp"]
    result = sorted(conversations.values(), key=lambda c: c["last_timestamp"], reverse=True)
    return JSONResponse({"conversations": result})

  @app.post("/api/send")
  def send_direct_message(payload: SendTextPayload, request: Request) -> JSONResponse:
    _require_login(request, app.state.container.settings)
    try:
      app.state.container.service.send_text_message(to_number=payload.to, body=payload.message)
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
      raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({"ok": True})

  @app.post("/api/send-media")
  async def send_media_message(
    request: Request,
    to: str = Form(...),
    caption: str = Form(default=""),
    file: UploadFile = File(...),
  ) -> JSONResponse:
    _require_login(request, app.state.container.settings)
    content = await file.read()
    mime_type = file.content_type or "application/octet-stream"
    filename = file.filename or "upload"
    try:
      app.state.container.service.send_media_message(
        to_number=to,
        file_bytes=content,
        filename=filename,
        mime_type=mime_type,
        caption=caption,
      )
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
      raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({"ok": True})

  # -- WhatsApp webhook + cron (dormant until Meta/OpenAI creds land) ----------

  @app.get("/webhook")
  def webhook_verify(request: Request) -> Response:
    is_valid, body = app.state.container.whatsapp.verify_get(request.query_params)
    status_code = 200 if is_valid else 403
    return PlainTextResponse(body, status_code=status_code)

  @app.post("/webhook")
  async def webhook_receive(request: Request) -> Response:
    raw_body = await request.body()
    if not raw_body:
      return PlainTextResponse("OK")
    try:
      payload = json.loads(raw_body)
    except json.JSONDecodeError:
      return PlainTextResponse("OK")

    try:
      incoming = app.state.container.whatsapp.parse_incoming_message(payload)
      if incoming is not None:
        app.state.container.service.handle_incoming_message(incoming)
    except Exception:
      LOGGER.exception("WEBHOOK processing failed")

    return PlainTextResponse("OK")

  @app.post("/api/cron/daily-followup")
  def cron_daily_followup(request: Request) -> JSONResponse:
    settings = app.state.container.settings
    _require_cron_secret(request, settings)
    app.state.container.service.daily_follow_up_check()
    return JSONResponse({"ok": True})

  return app


# -- JSON serialisation ------------------------------------------------------


def _jsonify(value: Any) -> Any:
  if isinstance(value, Enum):
    return value.value
  if isinstance(value, dict):
    return {k: _jsonify(v) for k, v in value.items()}
  if isinstance(value, list):
    return [_jsonify(v) for v in value]
  return value


def _serialise(obj: Any) -> dict[str, Any]:
  if not is_dataclass(obj):
    raise TypeError(f"_serialise expects a dataclass instance, got {type(obj)!r}")
  return _jsonify(asdict(obj))


def _serialise_doer_view(view: Any) -> dict[str, Any]:
  data = _serialise(view)
  data["effective_due_date"] = view.effective_due_date
  data["is_completed"] = view.is_completed
  return data


# -- container / oauth wiring -------------------------------------------------


def _build_runtime_container(settings: Settings | None) -> "AppContainer":
  from ceod.container import build_container

  return build_container(settings)


def _build_oauth_client(settings: Settings) -> OAuth:
  oauth = OAuth()
  oauth.register(
    name="google",
    client_id=settings.google_oauth_client_id,
    client_secret=settings.google_oauth_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
  )
  return oauth


def _oauth_redirect_uri(request: Request, settings: Settings) -> str:
  if settings.public_base_url:
    return f"{settings.public_base_url.rstrip('/')}/auth/callback"
  return str(request.url_for("auth_callback"))


def _is_public_path(path: str) -> bool:
  return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


# -- session / login ------------------------------------------------------


def _get_serializer(settings: Any) -> URLSafeTimedSerializer:
  secret = getattr(settings, "session_secret", None) or "ceod-fallback-secret-change-me"
  return URLSafeTimedSerializer(secret, salt="ceod-session")


def _make_session_token(login: LoginResult, settings: Any) -> str:
  payload = {"email": login.email, "name": login.name, "role": login.role.value, "department": login.department}
  return _get_serializer(settings).dumps(payload)


def _current_login(request: Request, settings: Any) -> LoginResult | None:
  token = request.cookies.get(SESSION_COOKIE)
  if not token:
    return None
  try:
    payload = _get_serializer(settings).loads(token, max_age=SESSION_MAX_AGE)
  except (SignatureExpired, BadSignature):
    return None
  try:
    return LoginResult(
      email=payload["email"],
      name=payload["name"],
      role=UserRole(payload["role"]),
      department=payload.get("department"),
    )
  except (KeyError, ValueError, TypeError):
    return None


def _require_login(request: Request, settings: Any) -> LoginResult:
  login = _current_login(request, settings)
  if login is None:
    raise HTTPException(status_code=401, detail="Not signed in")
  return login


def _require_management(login: LoginResult) -> None:
  if not login.is_management:
    raise HTTPException(status_code=403, detail="This action requires a TL, Manager, Senior, or Admin account")


def _require_admin(login: LoginResult) -> None:
  if login.role != UserRole.ADMIN:
    raise HTTPException(status_code=403, detail="This action requires an Admin account")


def _require_cron_secret(request: Request, settings: Any) -> None:
  expected = getattr(settings, "cron_secret", None)
  if not expected:
    raise HTTPException(status_code=503, detail="CRON_SECRET is not configured")
  provided = request.headers.get("x-cron-secret", "")
  if not hmac.compare_digest(provided, expected):
    raise HTTPException(status_code=401, detail="Invalid cron secret")
