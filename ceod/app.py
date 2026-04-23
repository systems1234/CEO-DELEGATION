from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ceod.config import Settings

try:
  from apscheduler.schedulers.background import BackgroundScheduler as _BackgroundScheduler
  BackgroundScheduler = _BackgroundScheduler
except ImportError:  # pragma: no cover - exercised only in minimal local envs
  BackgroundScheduler = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
  from ceod.container import AppContainer

LOGGER = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).resolve().parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
PROTECTED_DASHBOARD_PATHS = frozenset({"/", "/chat", "/api/dashboard", "/api/tasks", "/api/chats", "/api/send", "/api/send-media"})
AUTH_CHALLENGE_HEADER = 'Basic realm="CEO Mission Control", charset="UTF-8"'


class TaskCreatePayload(BaseModel):
  assignee: str = Field(min_length=1)
  task: str = Field(min_length=1)
  due_date: str | None = None


class SendTextPayload(BaseModel):
  to: str = Field(min_length=1)
  message: str = Field(min_length=1)


def create_app(settings: Settings | None = None, container: "AppContainer" | None = None) -> FastAPI:
  logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    force=True,
  )
  logging.getLogger("ceod").setLevel(logging.DEBUG)
  LOGGER.info("Logging initialised — level=DEBUG")

  resolved_container = container or _build_runtime_container(settings)
  scheduler = _build_scheduler(resolved_container)

  @asynccontextmanager
  async def lifespan(app: FastAPI):
    app.state.container = resolved_container
    app.state.scheduler = scheduler
    if scheduler and not scheduler.running:
      scheduler.start()
      LOGGER.info("Daily scheduler started")
    try:
      yield
    finally:
      if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
      app.state.container.close()

  app = FastAPI(title="CEO Delegation Service", version="1.0.0", lifespan=lifespan)
  app.state.container = resolved_container
  app.state.scheduler = scheduler
  app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")

  @app.middleware("http")
  async def protect_dashboard_routes(request: Request, call_next):
    if _is_protected_dashboard_path(request.url.path):
      auth_failure = _validate_dashboard_access(request, app.state.container.settings)
      if auth_failure is not None:
        return auth_failure

    response = await call_next(request)
    if _is_protected_dashboard_path(request.url.path):
      response.headers["Cache-Control"] = "no-store"
    return response

  @app.get("/healthz")
  def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})

  @app.get("/", response_class=HTMLResponse)
  def dashboard(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
      request=request,
      name="dashboard.html",
      context={
        "app_title": "CEO Mission Control",
        "api_dashboard_url": "/api/dashboard",
        "api_task_url": "/api/tasks",
      },
    )

  @app.get("/api/dashboard")
  def dashboard_snapshot() -> JSONResponse:
    snapshot = app.state.container.service.get_dashboard_snapshot()
    return JSONResponse(_serialise_snapshot(snapshot))

  @app.post("/api/tasks", status_code=201)
  def assign_task(payload: TaskCreatePayload) -> JSONResponse:
    try:
      task = app.state.container.service.assign_task(
        assignee=payload.assignee,
        task=payload.task,
        due_date=payload.due_date,
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
  return task


def _build_scheduler(container: "AppContainer") -> Any:
  if not container.settings.scheduler_enabled:
    return None

  scheduler = BackgroundScheduler(timezone=container.settings.script_timezone)
  scheduler.add_job(
    container.service.daily_follow_up_check,
    trigger="cron",
    hour=container.settings.scheduler_hour_ist,
    minute=container.settings.scheduler_minute_ist,
    id="daily_follow_up_check",
    replace_existing=True,
  )
  return scheduler


def _build_runtime_container(settings: Settings | None) -> "AppContainer":
  from ceod.container import build_container

  return build_container(settings)


def _is_protected_dashboard_path(path: str) -> bool:
  return path in PROTECTED_DASHBOARD_PATHS


def _validate_dashboard_access(request: Request, settings: Any) -> PlainTextResponse | None:
  if not getattr(settings, "dashboard_auth_enabled", False):
    return None

  auth_header = request.headers.get("Authorization")
  credentials = _decode_basic_auth(auth_header)
  expected_username = getattr(settings, "dashboard_username", None)
  expected_password = getattr(settings, "dashboard_password", None)
  if not credentials or not expected_username or not expected_password:
    return _unauthorized_response()

  username, password = credentials
  if not (
    hmac.compare_digest(username, expected_username)
    and hmac.compare_digest(password, expected_password)
  ):
    return _unauthorized_response()
  return None


def _decode_basic_auth(auth_header: str | None) -> tuple[str, str] | None:
  if not auth_header:
    return None

  scheme, _, token = auth_header.partition(" ")
  if scheme.lower() != "basic" or not token:
    return None

  try:
    raw_credentials = base64.b64decode(token, validate=True).decode("utf-8")
  except (binascii.Error, UnicodeDecodeError):
    return None

  username, separator, password = raw_credentials.partition(":")
  if not separator:
    return None
  return username, password


def _unauthorized_response() -> PlainTextResponse:
  return PlainTextResponse(
    "Authentication required",
    status_code=401,
    headers={
      "WWW-Authenticate": AUTH_CHALLENGE_HEADER,
      "Cache-Control": "no-store",
    },
  )
