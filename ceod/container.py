from __future__ import annotations

from dataclasses import dataclass

from ceod.config import Settings
from ceod.parser import OpenAIMessageParser
from ceod.service import TaskDelegationService
from ceod.sheets import GoogleSheetsRepository
from ceod.whatsapp import GreenAPIWhatsAppGateway, MetaWhatsAppGateway, build_whatsapp_gateway


@dataclass
class AppContainer:
  settings: Settings
  repository: GoogleSheetsRepository
  parser: OpenAIMessageParser
  whatsapp: MetaWhatsAppGateway | GreenAPIWhatsAppGateway
  service: TaskDelegationService

  def close(self) -> None:
    self.parser.close()
    self.whatsapp.close()


def build_container(settings: Settings | None = None) -> AppContainer:
  resolved_settings = settings or Settings()
  repository = GoogleSheetsRepository(resolved_settings)
  repository.ensure_base_sheets()
  parser = OpenAIMessageParser(resolved_settings)
  whatsapp = build_whatsapp_gateway(resolved_settings)
  service = TaskDelegationService(
    parser=parser,
    whatsapp=whatsapp,
    repository=repository,
    ceo_number=resolved_settings.ceo_wa_number,
    timezone_name=resolved_settings.script_timezone,
  )
  return AppContainer(
    settings=resolved_settings,
    repository=repository,
    parser=parser,
    whatsapp=whatsapp,
    service=service,
  )

