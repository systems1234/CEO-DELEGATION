from __future__ import annotations

import base64
import logging
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

import httpx

from ceod.config import Settings
from ceod.exceptions import ExternalServiceError
from ceod.models import IncomingMessage
from ceod.utils import normalise_whatsapp_number


_AUDIO_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
_IMAGE_ANALYSIS_PROMPT = (
  "Read this WhatsApp image carefully. Extract the useful plain-text content and any clear task-style "
  "instructions visible in the image. If there is a caption, incorporate it naturally. Preserve names, "
  "dates, deadlines, and action items. Return only plain text. If there is no useful readable content, "
  "return an empty string."
)
_VIDEO_FRAME_LIMIT = 4
_AUDIO_EXTENSION_BY_MIME_TYPE = {
  "audio/aac": "aac",
  "audio/amr": "amr",
  "audio/m4a": "m4a",
  "audio/mp3": "mp3",
  "audio/mp4": "mp4",
  "audio/mpeg": "mp3",
  "audio/ogg": "ogg",
  "audio/wav": "wav",
  "audio/webm": "webm",
}
_VIDEO_EXTENSION_BY_MIME_TYPE = {
  "video/3gpp": "3gp",
  "video/mp4": "mp4",
  "video/mpeg": "mpeg",
  "video/quicktime": "mov",
  "video/webm": "webm",
  "video/x-msvideo": "avi",
}


class _InboundMediaAnalysisMixin:
  _settings: Settings
  _logger: logging.Logger
  _client: httpx.Client

  def _transcribe_audio_bytes(self, audio_bytes: bytes, *, mime_type: str) -> str:
    if not audio_bytes:
      return ""

    cleaned_mime_type = (mime_type or "audio/ogg").split(";", 1)[0].strip().lower() or "audio/ogg"
    filename = f"voice-note.{_AUDIO_EXTENSION_BY_MIME_TYPE.get(cleaned_mime_type, 'ogg')}"

    try:
      whisper_resp = self._client.post(
        _AUDIO_TRANSCRIPTION_URL,
        headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
        files={"file": (filename, audio_bytes, cleaned_mime_type)},
        data={"model": "whisper-1"},
      )
      whisper_resp.raise_for_status()
      transcript = str(whisper_resp.json().get("text", "")).strip()
      self._logger.info("Voice note transcribed len=%d chars", len(transcript))
      return transcript
    except Exception as exc:
      self._logger.warning("Voice note transcription failed: %s", exc)
      return ""

  def _extract_image_text(self, image_bytes: bytes, *, mime_type: str, caption: str = "") -> str:
    if not image_bytes and not caption.strip():
      return ""

    cleaned_mime_type = (mime_type or "image/jpeg").split(";", 1)[0].strip().lower() or "image/jpeg"
    content: list[dict[str, object]] = [
      {
        "type": "text",
        "text": _IMAGE_ANALYSIS_PROMPT + (f"\n\nCaption: {caption.strip()}" if caption.strip() else ""),
      }
    ]
    if image_bytes:
      encoded_image = base64.b64encode(image_bytes).decode("ascii")
      content.append(
        {
          "type": "image_url",
          "image_url": {
            "url": f"data:{cleaned_mime_type};base64,{encoded_image}",
          },
        }
      )

    vision_model = "gpt-4o"
    payload = {
      "model": vision_model,
      "temperature": 0,
      "max_tokens": 300,
      "messages": [{"role": "user", "content": content}],
    }

    self._logger.info("Vision request model=%s image_bytes=%d caption=%r",
                      vision_model, len(image_bytes), caption[:80])
    try:
      response = self._client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
        json=payload,
        timeout=30.0,
      )
      if not response.is_success:
        self._logger.warning("Vision API error status=%s body=%s",
                             response.status_code, response.text[:500])
        return caption.strip()
      extracted = self._read_chat_message_text(response.json()).strip()
      self._logger.info("Vision extracted text=%r", extracted[:120])
      return extracted
    except Exception as exc:
      self._logger.warning("Vision request failed: %s", exc)
      return caption.strip()

  def _extract_video_text(self, video_bytes: bytes, *, mime_type: str, caption: str = "") -> str:
    if not video_bytes and not caption.strip():
      return ""

    try:
      import imageio_ffmpeg
    except ImportError:
      self._logger.warning("Video analysis unavailable: imageio-ffmpeg is not installed")
      return caption.strip()

    cleaned_mime_type = (mime_type or "video/mp4").split(";", 1)[0].strip().lower() or "video/mp4"
    video_extension = _VIDEO_EXTENSION_BY_MIME_TYPE.get(cleaned_mime_type, "mp4")

    with tempfile.TemporaryDirectory(prefix="ceod-video-") as temp_dir:
      temp_path = Path(temp_dir)
      video_path = temp_path / f"input.{video_extension}"
      audio_path = temp_path / "audio.wav"
      frame_pattern = temp_path / "frame-%03d.jpg"
      video_path.write_bytes(video_bytes)

      ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
      self._extract_video_audio_track(ffmpeg_exe, video_path, audio_path)
      self._extract_video_frames(ffmpeg_exe, video_path, frame_pattern)

      parts: list[str] = []
      cleaned_caption = caption.strip()
      if cleaned_caption:
        parts.append(f"Caption: {cleaned_caption}")

      audio_text = ""
      if audio_path.exists() and audio_path.stat().st_size > 0:
        audio_text = self._transcribe_audio_bytes(audio_path.read_bytes(), mime_type="audio/wav")
      if audio_text:
        parts.append(f"Audio transcript: {audio_text}")

      frame_texts = self._extract_video_frame_texts(temp_path)
      if frame_texts:
        parts.extend(f"Frame {index}: {frame_text}" for index, frame_text in enumerate(frame_texts, start=1))

      return "\n\n".join(parts).strip()

  def _extract_video_audio_track(self, ffmpeg_exe: str, video_path: Path, audio_path: Path) -> None:
    command = [
      ffmpeg_exe,
      "-y",
      "-i",
      str(video_path),
      "-vn",
      "-ac",
      "1",
      "-ar",
      "16000",
      str(audio_path),
    ]
    self._run_ffmpeg_command(command, "video audio extraction")

  def _extract_video_frames(self, ffmpeg_exe: str, video_path: Path, frame_pattern: Path) -> None:
    command = [
      ffmpeg_exe,
      "-y",
      "-i",
      str(video_path),
      "-vf",
      "fps=1/3,scale='min(1280,iw)':-2",
      "-frames:v",
      str(_VIDEO_FRAME_LIMIT),
      str(frame_pattern),
    ]
    self._run_ffmpeg_command(command, "video frame extraction")

  def _run_ffmpeg_command(self, command: list[str], operation: str) -> None:
    try:
      completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
      )
    except Exception as exc:
      self._logger.warning("%s failed to start: %s", operation, exc)
      return

    if completed.returncode != 0:
      stderr = completed.stderr.strip().splitlines()
      tail = stderr[-1] if stderr else "unknown ffmpeg error"
      self._logger.warning("%s failed: %s", operation, tail)

  def _extract_video_frame_texts(self, temp_path: Path) -> list[str]:
    frame_texts: list[str] = []
    for frame_path in sorted(temp_path.glob("frame-*.jpg"))[:_VIDEO_FRAME_LIMIT]:
      try:
        frame_text = self._extract_image_text(frame_path.read_bytes(), mime_type="image/jpeg")
      except Exception as exc:
        self._logger.warning("Video frame analysis failed for %s: %s", frame_path.name, exc)
        continue
      if frame_text:
        frame_texts.append(frame_text)
    return frame_texts

  def _read_chat_message_text(self, payload: object) -> str:
    if not isinstance(payload, Mapping):
      return ""
    try:
      content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
      return ""
    if isinstance(content, str):
      return content
    if isinstance(content, list):
      text_parts = [
        str(part.get("text", ""))
        for part in content
        if isinstance(part, Mapping) and part.get("type") == "text"
      ]
      return "".join(text_parts)
    return str(content)


class MetaWhatsAppGateway(_InboundMediaAnalysisMixin):
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
    message_type = str(message.get("type", "")).strip()
    if message_type == "text":
      text = str(message.get("text", {}).get("body", "")).strip()
    elif message_type == "audio":
      self._logger.info("META audio message received - transcribing")
      text = self._transcribe_meta_audio(message)
      self._logger.info("META audio transcription result=%r", text[:120])
    elif message_type == "image":
      self._logger.info("META image message received - extracting text")
      text = self._extract_meta_image_text(message)
      self._logger.info("META image extracted text=%r", text[:120])
    elif message_type == "video":
      self._logger.info("META video message received - extracting text")
      text = self._extract_meta_video_text(message)
      self._logger.info("META video extracted text=%r", text[:120])
    else:
      self._logger.info("Ignoring Meta webhook type=%s", message_type)
      return None

    if not text:
      return None

    return IncomingMessage(
      from_number=normalise_whatsapp_number(str(message.get("from", ""))),
      text=text,
    )

  def _transcribe_meta_audio(self, message: Mapping[str, object]) -> str:
    audio_data = message.get("audio")
    if not isinstance(audio_data, Mapping):
      self._logger.warning("Meta audio message missing audio payload")
      return ""

    media_id = str(audio_data.get("id", "")).strip()
    if not media_id:
      self._logger.warning("Meta audio message missing media id")
      return ""

    mime_type_hint = str(audio_data.get("mime_type", "")).strip()
    return self._transcribe_audio_bytes(
      self._download_meta_media(media_id),
      mime_type=mime_type_hint or "audio/ogg",
    )

  def _extract_meta_image_text(self, message: Mapping[str, object]) -> str:
    image_data = message.get("image")
    if not isinstance(image_data, Mapping):
      self._logger.warning("Meta image message missing image payload")
      return ""

    media_id = str(image_data.get("id", "")).strip()
    if not media_id:
      self._logger.warning("Meta image message missing media id")
      return ""

    caption = str(image_data.get("caption", "")).strip()
    mime_type_hint = str(image_data.get("mime_type", "")).strip() or "image/jpeg"
    return self._extract_image_text(
      self._download_meta_media(media_id),
      mime_type=mime_type_hint,
      caption=caption,
    )

  def _extract_meta_video_text(self, message: Mapping[str, object]) -> str:
    video_data = message.get("video")
    if not isinstance(video_data, Mapping):
      self._logger.warning("Meta video message missing video payload")
      return ""

    media_id = str(video_data.get("id", "")).strip()
    if not media_id:
      self._logger.warning("Meta video message missing media id")
      return ""

    caption = str(video_data.get("caption", "")).strip()
    mime_type_hint = str(video_data.get("mime_type", "")).strip() or "video/mp4"
    return self._extract_video_text(
      self._download_meta_media(media_id),
      mime_type=mime_type_hint,
      caption=caption,
    )

  def _download_meta_media(self, media_id: str) -> bytes:
    auth_headers = {"Authorization": f"Bearer {self._settings.wa_access_token}"}
    metadata_url = (
      f"{self._settings.meta_api_base.rstrip('/')}/"
      f"{self._settings.meta_api_version}/{media_id}"
    )

    try:
      metadata_resp = self._client.get(metadata_url, headers=auth_headers)
      metadata_resp.raise_for_status()
      download_url = str(metadata_resp.json().get("url", "")).strip()
      if not download_url:
        self._logger.warning("Meta media has no downloadable URL for media id=%s", media_id)
        return b""

      media_resp = self._client.get(download_url, headers=auth_headers)
      media_resp.raise_for_status()
      return media_resp.content
    except httpx.HTTPError as exc:
      self._logger.warning("Meta media download failed for media id=%s: %s", media_id, exc)
      return b""

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


class GreenAPIWhatsAppGateway(_InboundMediaAnalysisMixin):
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
    self._logger.info("GREEN_API typeWebhook=%s", type_webhook)

    if type_webhook != "incomingMessageReceived":
      self._logger.info("GREEN_API ignoring - not incomingMessageReceived")
      return None

    message_data = payload.get("messageData", {})
    message_type = str(message_data.get("typeMessage", ""))
    sender_data = payload.get("senderData", {})
    chat_id = str(sender_data.get("chatId") or "")
    sender = str(sender_data.get("sender") or "")
    id_message = str(payload.get("idMessage", ""))

    self._logger.info(
      "GREEN_API messageType=%s chatId=%s sender=%s idMessage=%s",
      message_type, chat_id, sender, id_message,
    )

    text = ""
    if message_type == "textMessage":
      text = str(message_data.get("textMessageData", {}).get("textMessage", "")).strip()
      self._logger.info("GREEN_API text message text=%r", text[:120])
    elif message_type in {"quotedMessage", "extendedTextMessage"}:
      text = str(message_data.get("extendedTextMessageData", {}).get("text", "")).strip()
      self._logger.info("GREEN_API extended/quoted text=%r", text[:120])
    elif message_type in {"pttMessage", "audioMessage"}:
      self._logger.info("GREEN_API voice note - transcribing")
      text = self._transcribe_voice_note(chat_id, id_message)
      self._logger.info("GREEN_API transcription result=%r", text[:120])
    elif message_type == "imageMessage":
      self._logger.info("GREEN_API image message - analysing with vision")
      text = self._extract_greenapi_image_text(chat_id, id_message, message_data)
      self._logger.info("GREEN_API image extracted text=%r", text[:120])
    elif message_type == "videoMessage":
      self._logger.info("GREEN_API video message - transcribing")
      text = self._extract_greenapi_video_text(chat_id, id_message, message_data)
      self._logger.info("GREEN_API video extracted text=%r", text[:120])
    else:
      self._logger.info("GREEN_API ignoring unsupported messageType=%s", message_type)
      return None

    if not text:
      self._logger.info("GREEN_API empty text after extraction - ignoring")
      return None

    raw_sender = sender or chat_id
    normalised = normalise_whatsapp_number(raw_sender)
    self._logger.info("GREEN_API resolved from_number=%s", normalised)
    return IncomingMessage(from_number=normalised, text=text)

  def _transcribe_voice_note(self, chat_id: str, id_message: str) -> str:
    audio_bytes, mime_type = self._download_greenapi_media(chat_id, id_message, fallback_mime_type="audio/ogg")
    if not audio_bytes:
      return ""
    return self._transcribe_audio_bytes(audio_bytes, mime_type=mime_type)

  def _extract_greenapi_image_text(
    self,
    chat_id: str,
    id_message: str,
    message_data: Mapping[str, object],
  ) -> str:
    image_bytes, mime_type = self._download_greenapi_media(chat_id, id_message, fallback_mime_type="image/jpeg")
    self._logger.info("Image download: %d bytes mime=%s", len(image_bytes), mime_type)
    return self._extract_image_text(
      image_bytes,
      mime_type=mime_type,
      caption=self._extract_greenapi_image_caption(message_data),
    )

  def _extract_greenapi_video_text(
    self,
    chat_id: str,
    id_message: str,
    message_data: Mapping[str, object],
  ) -> str:
    video_bytes, mime_type = self._download_greenapi_media(chat_id, id_message, fallback_mime_type="video/mp4")
    return self._extract_video_text(
      video_bytes,
      mime_type=mime_type,
      caption=self._extract_greenapi_media_caption(message_data),
    )

  def _extract_greenapi_image_caption(self, message_data: Mapping[str, object]) -> str:
    return self._extract_greenapi_media_caption(message_data, keys=("imageMessageData", "fileMessageData"))

  def _extract_greenapi_media_caption(
    self,
    message_data: Mapping[str, object],
    *,
    keys: tuple[str, ...] = ("imageMessageData", "videoMessageData", "fileMessageData"),
  ) -> str:
    for key in keys:
      nested = message_data.get(key)
      if not isinstance(nested, Mapping):
        continue
      caption = str(nested.get("caption", "")).strip()
      if caption:
        return caption
    return ""

  def _download_greenapi_media(
    self,
    chat_id: str,
    id_message: str,
    *,
    fallback_mime_type: str,
  ) -> tuple[bytes, str]:
    try:
      download_url_resp = self._client.post(
        (
          f"{self._settings.green_api_url.rstrip('/')}/"
          f"waInstance{self._settings.green_api_instance_id}/"
          f"downloadFile/{self._settings.green_api_token}"
        ),
        json={"chatId": chat_id, "idMessage": id_message},
      )
      download_url_resp.raise_for_status()
      download_url = str(download_url_resp.json().get("downloadUrl", ""))
      if not download_url:
        self._logger.warning("Green API media download URL missing for chatId=%s idMessage=%s", chat_id, id_message)
        return b"", fallback_mime_type

      media_resp = self._client.get(download_url)
      media_resp.raise_for_status()
      mime_type = media_resp.headers.get("Content-Type", fallback_mime_type)
      return media_resp.content, mime_type
    except Exception as exc:
      self._logger.warning("Green API media download failed: %s", exc)
      return b"", fallback_mime_type

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

  def send_file(self, to_number: str, file_bytes: bytes, filename: str, mime_type: str, caption: str = "") -> str:
    chat_id = f"{normalise_whatsapp_number(to_number)}@c.us"
    url = (
      f"{self._settings.green_api_url.rstrip('/')}/"
      f"waInstance{self._settings.green_api_instance_id}/"
      f"sendFileByUpload/{self._settings.green_api_token}"
    )
    try:
      response = self._client.post(
        url,
        data={"chatId": chat_id, "caption": caption},
        files={"file": (filename, file_bytes, mime_type)},
        timeout=60.0,
      )
      response.raise_for_status()
    except httpx.HTTPError as exc:
      raise ExternalServiceError(f"Green API file send failed: {exc}") from exc

    try:
      return str(response.json()["idMessage"])
    except (KeyError, TypeError, ValueError) as exc:
      raise ExternalServiceError("Green API returned an invalid response payload") from exc


def build_whatsapp_gateway(settings: Settings) -> MetaWhatsAppGateway | GreenAPIWhatsAppGateway:
  if settings.wa_provider == "meta":
    return MetaWhatsAppGateway(settings)
  return GreenAPIWhatsAppGateway(settings)
