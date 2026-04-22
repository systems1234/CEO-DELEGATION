from __future__ import annotations

import json
import logging
import re

import httpx

from ceod.config import Settings
from ceod.exceptions import ExternalServiceError
from ceod.models import ParsedPostponeReply, ParsedTaskAssignment
from ceod.utils import parse_date_flexible, today_iso

STRUCTURED_POSTPONE_RE = re.compile(
  r"new\s*date\s*[:\-]\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})[^\w]*reason\s*[:\-]\s*(.+)",
  re.IGNORECASE,
)


class OpenAIMessageParser:
  def __init__(self, settings: Settings) -> None:
    self._settings = settings
    self._logger = logging.getLogger(__name__)
    self._client = httpx.Client(timeout=settings.http_timeout_seconds)

  def close(self) -> None:
    self._client.close()

  def parse_task_assignment(
    self,
    raw_message: str,
    known_names: list[str],
  ) -> ParsedTaskAssignment | None:
    if not raw_message.strip():
      return None

    system_prompt = (
      "You are a task extraction assistant for an Indian business WhatsApp group. "
      "The CEO sends task assignments in Hindi, Hinglish, or English. "
      "Extract the assignment details and return only valid JSON.\n\n"
      f"Known team member names: {json.dumps(known_names)}\n\n"
      "Return this exact JSON shape:\n"
      "{\n"
      '  "assignee": "<name from the known list, best fuzzy match, or null if unclear>",\n'
      '  "task": "<clean task description in English>",\n'
      '  "due_date": "<YYYY-MM-DD or null if not mentioned>"\n'
      "}\n\n"
      "Rules:\n"
      f'- Today is {today_iso(self._settings.script_timezone)}. Resolve relative dates against today.\n'
      '- If the message is not a task assignment, return {"assignee": null, "task": null, "due_date": null}.\n'
    )

    payload = {
      "model": self._settings.openai_model,
      "temperature": 0,
      "max_tokens": 256,
      "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": raw_message},
      ],
      "response_format": {"type": "json_object"},
    }

    parsed = self._post_chat_completion(payload)
    if not parsed.get("assignee") or not parsed.get("task"):
      return None

    return ParsedTaskAssignment(
      assignee=str(parsed["assignee"]).strip(),
      task=str(parsed["task"]).strip(),
      due_date=str(parsed["due_date"]).strip() if parsed.get("due_date") else None,
    )

  def parse_postpone_reply(self, reply_text: str) -> ParsedPostponeReply:
    if not reply_text.strip():
      return ParsedPostponeReply(new_date=None, reason=None)

    match = STRUCTURED_POSTPONE_RE.search(reply_text)
    if match:
      return ParsedPostponeReply(
        new_date=parse_date_flexible(match.group(1).strip()),
        reason=match.group(2).strip() or None,
      )

    payload = {
      "model": self._settings.openai_model,
      "temperature": 0,
      "max_tokens": 128,
      "messages": [
        {
          "role": "system",
          "content": (
            "Extract a postpone date and reason from this WhatsApp reply. "
            f"Today is {today_iso(self._settings.script_timezone)}. "
            'Reply only with valid JSON: {"new_date": "YYYY-MM-DD or null", "reason": "string or null"}'
          ),
        },
        {"role": "user", "content": reply_text},
      ],
      "response_format": {"type": "json_object"},
    }

    parsed = self._post_chat_completion(payload)
    new_date = str(parsed["new_date"]).strip() if parsed.get("new_date") else None
    reason = str(parsed["reason"]).strip() if parsed.get("reason") else None
    return ParsedPostponeReply(new_date=new_date, reason=reason)

  def _post_chat_completion(self, payload: dict[str, object]) -> dict[str, object]:
    try:
      response = self._client.post(
        self._settings.openai_api_url,
        headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
        json=payload,
      )
      response.raise_for_status()
    except httpx.HTTPError as exc:
      raise ExternalServiceError(f"OpenAI request failed: {exc}") from exc

    try:
      content = response.json()["choices"][0]["message"]["content"]
      if isinstance(content, list):
        text_parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        content = "".join(text_parts)
      return json.loads(str(content).replace("```json", "").replace("```", "").strip())
    except (KeyError, IndexError, TypeError, ValueError) as exc:
      self._logger.exception("Failed to parse OpenAI response")
      raise ExternalServiceError("OpenAI returned an invalid response payload") from exc

