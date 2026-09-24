from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
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
from ceod.models import TaskPriority

if TYPE_CHECKING:
  from ceod.container import AppContainer

LOGGER = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).resolve().parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
PROTECTED_DASHBOARD_PATHS = frozenset({
  "/", "/board", "/my-tasks", "/reports", "/chat",
  "/api/dashboard", "/api/tasks", "/api/chats", "/api/send", "/api/send-media", "/api/members",
  "/api/triage", "/api/my-tasks", "/api/reports",
})
PROTECTED_DASHBOARD_PREFIXES = ("/api/tasks/",)
SESSION_COOKIE = "ceod_session"
SESSION_MAX_AGE = 60 * 60 * 24  # 24 hours


class TaskCreatePayload(BaseModel):
  assignee: str = Field(min_length=1)
  task: str = Field(min_length=1)
  due_date: str | None = None
  priority: str = "Medium"


class SendTextPayload(BaseModel):
  to: str = Field(min_length=1)
  message: str = Field(min_length=1)


class MemberCreatePayload(BaseModel):
  name: str = Field(min_length=1)
  number: str = Field(min_length=1)
  email: str = ""


class PostponePayload(BaseModel):
  new_date: str = Field(min_length=1)
  reason: str = Field(min_length=1)


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

  app = FastAPI(title="CEO Delegation Service", version="1.0.0", lifespan=lifespan)
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
    if _is_protected_dashboard_path(request.url.path):
      settings = app.state.container.settings
      if getattr(settings, "dashboard_auth_enabled", False):
        if not _has_valid_session(request, settings):
          next_url = request.url.path
          return RedirectResponse(url=f"/login?next={next_url}", status_code=302)

    response = await call_next(request)
    if _is_protected_dashboard_path(request.url.path):
      response.headers["Cache-Control"] = "no-store"
    return response

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
    if not email or not email_verified or not _is_allowed_google_account(email, settings):
      LOGGER.warning("Rejected Google sign-in for email=%s", email)
      return RedirectResponse(url="/login?error=Your+Google+account+is+not+authorised+for+this+dashboard.", status_code=302)

    token_value = _make_session_token(email, settings)
    response = RedirectResponse(url=next_url, status_code=302)
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

  @app.get("/", response_class=HTMLResponse)
  def home(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
      request=request,
      name="home.html",
      context={
        "app_title": "CEO Mission Control - Today",
        "active_page": "home",
        "page_heading": "Today's priorities",
        "page_subtitle": "Overdue and due-today work, grouped by date and priority.",
        "current_user_email": _current_session_email(request, app.state.container.settings),
      },
    )

  @app.get("/board", response_class=HTMLResponse)
  def board(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
      request=request,
      name="board.html",
      context={
        "app_title": "CEO Mission Control - Board",
        "api_dashboard_url": "/api/dashboard",
        "api_task_url": "/api/tasks",
        "active_page": "board",
        "page_heading": "Live operations",
        "page_subtitle": "Task routing, follow-up visibility, and WhatsApp actions in one board.",
        "current_user_email": _current_session_email(request, app.state.container.settings),
      },
    )

  @app.get("/my-tasks", response_class=HTMLResponse)
  def my_tasks_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
      request=request,
      name="my_tasks.html",
      context={
        "app_title": "CEO Mission Control - My Tasks",
        "active_page": "my-tasks",
        "page_heading": "My tasks",
        "page_subtitle": "Tasks assigned to you, with quick actions.",
        "current_user_email": _current_session_email(request, app.state.container.settings),
      },
    )

  @app.get("/reports", response_class=HTMLResponse)
  def reports_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
      request=request,
      name="reports.html",
      context={
        "app_title": "CEO Mission Control - Reports",
        "active_page": "reports",
        "page_heading": "KPI reports",
        "page_subtitle": "Completion rate, overdue load, and team performance.",
        "current_user_email": _current_session_email(request, app.state.container.settings),
      },
    )

  @app.get("/api/dashboard")
  def dashboard_snapshot() -> JSONResponse:
    snapshot = app.state.container.service.get_dashboard_snapshot()
    return JSONResponse(_serialise_snapshot(snapshot))

  @app.get("/api/triage")
  def triage_view() -> JSONResponse:
    view = app.state.container.service.get_triage_view()
    data = asdict(view)
    for group in data["overdue_groups"]:
      group["tasks"] = [_serialise_task_from_dict(t) for t in group["tasks"]]
    data["due_today"] = [_serialise_task_from_dict(t) for t in data["due_today"]]
    return JSONResponse(data)

  @app.get("/api/my-tasks")
  def my_tasks_api(request: Request) -> JSONResponse:
    settings = app.state.container.settings
    email = _current_session_email(request, settings)
    linked = bool(email and app.state.container.repository.get_member_by_email(email))
    tasks = app.state.container.service.get_my_tasks(email) if email else []
    return JSONResponse({
      "email": email,
      "linked": linked,
      "tasks": [_serialise_task(t) for t in tasks],
    })

  @app.get("/api/reports")
  def reports_api() -> JSONResponse:
    report = app.state.container.service.get_kpi_report()
    return JSONResponse(asdict(report))

  @app.post("/api/tasks", status_code=201)
  def assign_task(payload: TaskCreatePayload) -> JSONResponse:
    try:
      priority = TaskPriority(payload.priority)
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=f'Invalid priority "{payload.priority}"') from exc
    try:
      task = app.state.container.service.assign_task(
        assignee=payload.assignee,
        task=payload.task,
        due_date=payload.due_date,
        priority=priority,
        notify_ceo=False,
        notify_assignee=True,
      )
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc

    snapshot = app.state.container.service.get_dashboard_snapshot()
    return JSONResponse(
      {
        "task": _serialise_task(task),
        "stats": _serialise_snapshot(snapshot)["stats"],
      },
      status_code=201,
    )

  @app.post("/api/tasks/{row_id}/done")
  def mark_task_done(row_id: str) -> JSONResponse:
    try:
      task = app.state.container.service.complete_task(row_id=row_id)
    except ValueError as exc:
      raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse({"task": _serialise_task(task)})

  @app.post("/api/tasks/{row_id}/postpone")
  def postpone_task_api(row_id: str, payload: PostponePayload) -> JSONResponse:
    try:
      task = app.state.container.service.postpone_task_from_dashboard(
        row_id=row_id, new_date=payload.new_date, reason=payload.reason,
      )
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"task": _serialise_task(task)})

  @app.post("/api/members", status_code=201)
  def add_member(payload: MemberCreatePayload) -> JSONResponse:
    try:
      member = app.state.container.service.add_team_member(
        name=payload.name,
        number=payload.number,
        email=payload.email,
      )
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    snapshot = app.state.container.service.get_dashboard_snapshot()
    return JSONResponse(
      {
        "member": asdict(member),
        "stats": _serialise_snapshot(snapshot)["stats"],
        "members": [asdict(m) for m in snapshot.members],
      },
      status_code=201,
    )

  @app.get("/chat", response_class=HTMLResponse)
  def chat_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request=request, name="chat.html", context={"app_title": "CEO Mission Control - Chats"})

  @app.get("/api/chats")
  def get_chats() -> JSONResponse:
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
  def send_direct_message(payload: SendTextPayload) -> JSONResponse:
    try:
      app.state.container.service.send_text_message(to_number=payload.to, body=payload.message)
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
      raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({"ok": True})

  @app.post("/api/send-media")
  async def send_media_message(
    to: str = Form(...),
    caption: str = Form(default=""),
    file: UploadFile = File(...),
  ) -> JSONResponse:
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

  @app.get("/webhook")
  def webhook_verify(request: Request) -> Response:
    is_valid, body = app.state.container.whatsapp.verify_get(request.query_params)
    status_code = 200 if is_valid else 403
    return PlainTextResponse(body, status_code=status_code)

  @app.post("/webhook")
  async def webhook_receive(request: Request) -> Response:
    LOGGER.debug("WEBHOOK [1/6] hit — client=%s method=%s path=%s",
                 request.client.host if request.client else "unknown",
                 request.method, request.url.path)
    LOGGER.debug("WEBHOOK [2/6] headers=%s", dict(request.headers))

    raw_body = await request.body()
    LOGGER.debug("WEBHOOK [3/6] raw body (%d bytes): %s",
                 len(raw_body), raw_body.decode("utf-8", errors="replace"))

    if not raw_body:
      LOGGER.warning("WEBHOOK [3/6] empty body — ignoring")
      return PlainTextResponse("OK")

    try:
      payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
      LOGGER.warning("WEBHOOK [4/6] JSON parse failed: %s — raw=%s", exc,
                     raw_body.decode("utf-8", errors="replace")[:500])
      return PlainTextResponse("OK")

    LOGGER.debug("WEBHOOK [4/6] parsed JSON — type=%s keys=%s",
                 type(payload).__name__,
                 list(payload.keys()) if isinstance(payload, dict) else "n/a")
    LOGGER.debug("WEBHOOK [4/6] full payload: %s", json.dumps(payload, ensure_ascii=False))

    try:
      LOGGER.debug("WEBHOOK [5/6] calling parse_incoming_message")
      incoming = app.state.container.whatsapp.parse_incoming_message(payload)
      if incoming is not None:
        LOGGER.debug("WEBHOOK [5/6] parsed OK — from=%s text=%r", incoming.from_number, incoming.text[:120])
        LOGGER.debug("WEBHOOK [6/6] dispatching to service.handle_incoming_message")
        app.state.container.service.handle_incoming_message(incoming)
        LOGGER.debug("WEBHOOK [6/6] service handler returned OK")
      else:
        LOGGER.debug("WEBHOOK [5/6] parse_incoming_message returned None — message ignored")
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


def _serialise_snapshot(snapshot: object) -> dict[str, object]:
  data = asdict(snapshot)
  data["tasks"] = [_serialise_task_from_dict(task) for task in data["tasks"]]
  return data


def _serialise_task(task: object) -> dict[str, object]:
  data = asdict(task)
  return _serialise_task_from_dict(data)


def _serialise_task_from_dict(task: dict[str, object]) -> dict[str, object]:
  status = task["status"]
  task["status"] = status.value if hasattr(status, "value") else status
  priority = task.get("priority")
  if priority is not None:
    task["priority"] = priority.value if hasattr(priority, "value") else priority
  return task


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


def _is_protected_dashboard_path(path: str) -> bool:
  return path in PROTECTED_DASHBOARD_PATHS or path.startswith(PROTECTED_DASHBOARD_PREFIXES)


def _get_serializer(settings: Any) -> URLSafeTimedSerializer:
  secret = getattr(settings, "session_secret", None) or "ceod-fallback-secret-change-me"
  return URLSafeTimedSerializer(secret, salt="ceod-session")


def _make_session_token(email: str, settings: Any) -> str:
  return _get_serializer(settings).dumps(email)


def _has_valid_session(request: Request, settings: Any) -> bool:
  return _current_session_email(request, settings) is not None


def _current_session_email(request: Request, settings: Any) -> str | None:
  token = request.cookies.get(SESSION_COOKIE)
  if not token:
    return None
  try:
    return str(_get_serializer(settings).loads(token, max_age=SESSION_MAX_AGE))
  except (SignatureExpired, BadSignature):
    return None


def _is_allowed_google_account(email: str, settings: Any) -> bool:
  allowed_domain = getattr(settings, "allowed_google_domain", None)
  if allowed_domain and email.endswith(f"@{allowed_domain.strip().lower()}"):
    return True
  allowed_emails = getattr(settings, "allowed_google_emails", None) or ""
  allowlist = {addr.strip().lower() for addr in allowed_emails.split(",") if addr.strip()}
  return email in allowlist


def _require_cron_secret(request: Request, settings: Any) -> None:
  expected = getattr(settings, "cron_secret", None)
  if not expected:
    raise HTTPException(status_code=503, detail="CRON_SECRET is not configured")
  provided = request.headers.get("x-cron-secret", "")
  if not hmac.compare_digest(provided, expected):
    raise HTTPException(status_code=401, detail="Invalid cron secret")
