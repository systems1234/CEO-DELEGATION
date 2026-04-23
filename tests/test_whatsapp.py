from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ceod.whatsapp import GreenAPIWhatsAppGateway, MetaWhatsAppGateway


def build_settings() -> SimpleNamespace:
  return SimpleNamespace(
    openai_api_key="test-openai-key",
    openai_model="gpt-4o",
    openai_api_url="https://api.openai.com/v1/chat/completions",
    http_timeout_seconds=5.0,
    wa_verify_token="verify-token",
    wa_access_token="meta-access-token",
    wa_phone_number_id="1234567890",
    meta_api_base="https://graph.facebook.com",
    meta_api_version="v19.0",
    green_api_url="https://green.example.com",
    green_api_instance_id="12345",
    green_api_token="green-token",
  )


class MetaWhatsAppGatewayTests(unittest.TestCase):
  def test_parse_incoming_audio_transcribes_voice_note(self) -> None:
    gateway = MetaWhatsAppGateway(build_settings())
    self.addCleanup(gateway.close)
    gateway._transcribe_meta_audio = Mock(return_value="Rahul ko review task assign karo")

    payload = {
      "entry": [
        {
          "changes": [
            {
              "value": {
                "messages": [
                  {
                    "from": "919999999999",
                    "type": "audio",
                    "audio": {
                      "id": "media-1",
                      "mime_type": "audio/ogg",
                    },
                  }
                ]
              }
            }
          ]
        }
      ]
    }

    incoming = gateway.parse_incoming_message(payload)

    self.assertIsNotNone(incoming)
    assert incoming is not None
    self.assertEqual(incoming.from_number, "919999999999")
    self.assertEqual(incoming.text, "Rahul ko review task assign karo")
    gateway._transcribe_meta_audio.assert_called_once()

  def test_parse_incoming_image_extracts_text_from_image(self) -> None:
    gateway = MetaWhatsAppGateway(build_settings())
    self.addCleanup(gateway.close)
    gateway._extract_meta_image_text = Mock(return_value="Rahul ko monthly report bhejni hai")

    payload = {
      "entry": [
        {
          "changes": [
            {
              "value": {
                "messages": [
                  {
                    "from": "919999999999",
                    "type": "image",
                    "image": {
                      "id": "img-1",
                      "mime_type": "image/jpeg",
                      "caption": "Please handle this",
                    },
                  }
                ]
              }
            }
          ]
        }
      ]
    }

    incoming = gateway.parse_incoming_message(payload)

    self.assertIsNotNone(incoming)
    assert incoming is not None
    self.assertEqual(incoming.from_number, "919999999999")
    self.assertEqual(incoming.text, "Rahul ko monthly report bhejni hai")
    gateway._extract_meta_image_text.assert_called_once()

  def test_parse_incoming_video_extracts_text_from_video(self) -> None:
    gateway = MetaWhatsAppGateway(build_settings())
    self.addCleanup(gateway.close)
    gateway._extract_meta_video_text = Mock(return_value="Rahul ko client demo deck final karni hai")

    payload = {
      "entry": [
        {
          "changes": [
            {
              "value": {
                "messages": [
                  {
                    "from": "919999999999",
                    "type": "video",
                    "video": {
                      "id": "vid-1",
                      "mime_type": "video/mp4",
                      "caption": "Task in video",
                    },
                  }
                ]
              }
            }
          ]
        }
      ]
    }

    incoming = gateway.parse_incoming_message(payload)

    self.assertIsNotNone(incoming)
    assert incoming is not None
    self.assertEqual(incoming.from_number, "919999999999")
    self.assertEqual(incoming.text, "Rahul ko client demo deck final karni hai")
    gateway._extract_meta_video_text.assert_called_once()


class GreenAPIWhatsAppGatewayTests(unittest.TestCase):
  def test_parse_incoming_voice_note_transcribes_audio_message(self) -> None:
    gateway = GreenAPIWhatsAppGateway(build_settings())
    self.addCleanup(gateway.close)
    gateway._transcribe_voice_note = Mock(return_value="Rahul ko testing task de do")

    payload = {
      "typeWebhook": "incomingMessageReceived",
      "idMessage": "msg-1",
      "senderData": {
        "chatId": "919999999999@c.us",
        "sender": "919999999999@c.us",
      },
      "messageData": {
        "typeMessage": "pttMessage",
      },
    }

    incoming = gateway.parse_incoming_message(payload)

    self.assertIsNotNone(incoming)
    assert incoming is not None
    self.assertEqual(incoming.from_number, "919999999999")
    self.assertEqual(incoming.text, "Rahul ko testing task de do")
    gateway._transcribe_voice_note.assert_called_once_with("919999999999@c.us", "msg-1")

  def test_parse_incoming_image_message_extracts_text(self) -> None:
    gateway = GreenAPIWhatsAppGateway(build_settings())
    self.addCleanup(gateway.close)
    gateway._extract_greenapi_image_text = Mock(return_value="Rahul ko vendor invoice check karna hai")

    payload = {
      "typeWebhook": "incomingMessageReceived",
      "idMessage": "img-1",
      "senderData": {
        "chatId": "919999999999@c.us",
        "sender": "919999999999@c.us",
      },
      "messageData": {
        "typeMessage": "imageMessage",
        "imageMessageData": {
          "caption": "Task in screenshot",
        },
      },
    }

    incoming = gateway.parse_incoming_message(payload)

    self.assertIsNotNone(incoming)
    assert incoming is not None
    self.assertEqual(incoming.from_number, "919999999999")
    self.assertEqual(incoming.text, "Rahul ko vendor invoice check karna hai")
    gateway._extract_greenapi_image_text.assert_called_once_with(
      "919999999999@c.us",
      "img-1",
      payload["messageData"],
    )

  def test_parse_incoming_video_message_extracts_text(self) -> None:
    gateway = GreenAPIWhatsAppGateway(build_settings())
    self.addCleanup(gateway.close)
    gateway._extract_greenapi_video_text = Mock(return_value="Rahul ko showroom walk-through review karna hai")

    payload = {
      "typeWebhook": "incomingMessageReceived",
      "idMessage": "vid-1",
      "senderData": {
        "chatId": "919999999999@c.us",
        "sender": "919999999999@c.us",
      },
      "messageData": {
        "typeMessage": "videoMessage",
        "videoMessageData": {
          "caption": "Task in clip",
        },
      },
    }

    incoming = gateway.parse_incoming_message(payload)

    self.assertIsNotNone(incoming)
    assert incoming is not None
    self.assertEqual(incoming.from_number, "919999999999")
    self.assertEqual(incoming.text, "Rahul ko showroom walk-through review karna hai")
    gateway._extract_greenapi_video_text.assert_called_once_with(
      "919999999999@c.us",
      "vid-1",
      payload["messageData"],
    )


if __name__ == "__main__":
  unittest.main()
