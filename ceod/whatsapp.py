from __future__ import annotations

import logging
from collections.abc import Mapping

import httpx

from ceod.config import Settings
from ceod.exceptions import ExternalServiceError
from ceod.models import IncomingMessage
from ceod.utils import normalise_whatsapp_number


class MetaWhatsAppGateway:
  def __init__(self, settings: Settings) -> None:
    self._settings = settings
    self._logger = logging.getLogger(__name__)
    self._client = httpx.Client(timeout=settings.http_timeout_seconds)

  def close(self) -> None:
    self._client.close()

  def verify_get(self, params: Mapping[str, str]) -> tuple[bool, str]:
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")
    if mode == "subscribe" and token == self._settings.wa_verify_token:
      return True, challenge
    return False, "Forbidden"

  def parse_incoming_message(self, payload: dict[str, object]) -> IncomingMessage | None:
    messages = (
      payload.get("entry", [{}])[0]
      .get("changes", [{}])[0]
      .get("value", {})
      .get("messages", [])
    )
    if not messages:
      return None

    message = messages[0]
    if message.get("type") != "text":
      self._logger.info("Ignoring Meta webhook type=%s", message.get("type"))
      return None

    text = str(message.get("text", {}).get("body", "")).strip()
    if not text:
      return None

    return IncomingMessage(
      from_number=normalise_whatsapp_number(str(message.get("from", ""))),
      text=text,
    )

  def send_text(self, to_number: str, body: str) -> str:
    url = (
      f"{self._settings.meta_api_base.rstrip('/')}/"
      f"{self._settings.meta_api_version}/{self._settings.wa_phone_number_id}/messages"
    )
    payload = {
      "messaging_product": "whatsapp",
      "to": normalise_whatsapp_number(to_number),
      "type": "text",
      "text": {"body": body[:4096]},
    }

    try:
      response = self._client.post(
        url,
        headers={"Authorization": f"Bearer {self._settings.wa_access_token}"},
        json=payload,
      )
      response.raise_for_status()
    except httpx.HTTPError as exc:
      raise ExternalServiceError(f"Meta WhatsApp send failed: {exc}") from exc

    try:
      return str(response.json()["messages"][0]["id"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
      raise ExternalServiceError("Meta WhatsApp returned an invalid response payload") from exc


class GreenAPIWhatsAppGateway:
  def __init__(self, settings: Settings) -> None:
    self._settings = settings
    self._logger = logging.getLogger(__name__)
    self._client = httpx.Client(timeout=settings.http_timeout_seconds)

  def close(self) -> None:
    self._client.close()

  def verify_get(self, params: Mapping[str, str]) -> tuple[bool, str]:
    return True, "OK"

  def parse_incoming_message(self, payload: dict[str, object]) -> IncomingMessage | None:
    type_webhook = str(payload.get("typeWebhook", ""))
    if type_webhook != "incomingMessageReceived":
      self._logger.info("Ignoring Green API webhook type=%s", type_webhook)
      return None

    message_data = payload.get("messageData", {})
    message_type = str(message_data.get("typeMessage", ""))
    text = ""
    if message_type == "textMessage":
      text = str(message_data.get("textMessageData", {}).get("textMessage", "")).strip()
    elif message_type in {"quotedMessage", "extendedTextMessage"}:
      text = str(message_data.get("extendedTextMessageData", {}).get("text", "")).strip()
    else:
      self._logger.info("Ignoring Green API message type=%s", message_type)
      return None

    if not text:
      return None

    sender_data = payload.get("senderData", {})
    raw_sender = str(sender_data.get("sender") or sender_data.get("chatId") or "")
    return IncomingMessage(from_number=normalise_whatsapp_number(raw_sender), text=text)

  def send_text(self, to_number: str, body: str) -> str:
    chat_id = f"{normalise_whatsapp_number(to_number)}@c.us"
    url = (
      f"{self._settings.green_api_url.rstrip('/')}/"
      f"waInstance{self._settings.green_api_instance_id}/"
      f"sendMessage/{self._settings.green_api_token}"
    )
    payload = {"chatId": chat_id, "message": body[:20000]}

    try:
      response = self._client.post(url, json=payload)
      response.raise_for_status()
    except httpx.HTTPError as exc:
      raise ExternalServiceError(f"Green API send failed: {exc}") from exc

    try:
      return str(response.json()["idMessage"])
    except (KeyError, TypeError, ValueError) as exc:
      raise ExternalServiceError("Green API returned an invalid response payload") from exc


def build_whatsapp_gateway(settings: Settings) -> MetaWhatsAppGateway | GreenAPIWhatsAppGateway:
  if settings.wa_provider == "meta":
    return MetaWhatsAppGateway(settings)
  return GreenAPIWhatsAppGateway(settings)

