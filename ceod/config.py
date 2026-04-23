from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ceod.exceptions import ConfigurationError


class Settings(BaseSettings):
  model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    case_sensitive=False,
  )

  openai_api_key: str = Field(alias="OPENAI_API_KEY")
  openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
  openai_api_url: str = Field(
    default="https://api.openai.com/v1/chat/completions",
    alias="OPENAI_API_URL",
  )

  ceo_wa_number: str = Field(alias="CEO_WA_NUMBER")
  wa_provider: Literal["meta", "greenapi"] = Field(default="meta", alias="WA_PROVIDER")

  wa_access_token: str | None = Field(default=None, alias="WA_ACCESS_TOKEN")
  wa_phone_number_id: str | None = Field(default=None, alias="WA_PHONE_NUMBER_ID")
  wa_verify_token: str | None = Field(default=None, alias="WA_VERIFY_TOKEN")
  meta_api_base: str = Field(default="https://graph.facebook.com", alias="META_API_BASE")
  meta_api_version: str = Field(default="v19.0", alias="META_API_VERSION")

  green_api_url: str | None = Field(default=None, alias="GREEN_API_URL")
  green_api_instance_id: str | None = Field(default=None, alias="GREEN_API_INSTANCE_ID")
  green_api_token: str | None = Field(default=None, alias="GREEN_API_TOKEN")
  green_api_allowed_group_id: str | None = Field(default=None, alias="GREEN_API_ALLOWED_GROUP_ID")

  spreadsheet_id: str = Field(alias="SPREADSHEET_ID")
  google_service_account_json: str | None = Field(default=None, alias="GOOGLE_SERVICE_ACCOUNT_JSON")
  google_application_credentials: str | None = Field(default=None, alias="GOOGLE_APPLICATION_CREDENTIALS")

  script_timezone: str = Field(default="Asia/Kolkata", alias="SCRIPT_TIMEZONE")
  http_timeout_seconds: float = Field(default=20.0, alias="HTTP_TIMEOUT_SECONDS")
  dashboard_auth_enabled: bool = Field(default=True, alias="DASHBOARD_AUTH_ENABLED")
  dashboard_username: str | None = Field(default=None, alias="DASHBOARD_USERNAME")
  dashboard_password: str | None = Field(default=None, alias="DASHBOARD_PASSWORD")

  scheduler_enabled: bool = Field(default=False, alias="SCHEDULER_ENABLED")
  scheduler_hour_ist: int = Field(default=9, alias="SCHEDULER_HOUR_IST")
  scheduler_minute_ist: int = Field(default=0, alias="SCHEDULER_MINUTE_IST")

  @model_validator(mode="after")
  def validate_required_fields(self) -> "Settings":
    if self.wa_provider == "meta":
      missing = [
        key
        for key, value in {
          "WA_ACCESS_TOKEN": self.wa_access_token,
          "WA_PHONE_NUMBER_ID": self.wa_phone_number_id,
          "WA_VERIFY_TOKEN": self.wa_verify_token,
        }.items()
        if not value
      ]
      if missing:
        raise ConfigurationError(f"Missing Meta configuration: {', '.join(missing)}")

    if self.wa_provider == "greenapi":
      missing = [
        key
        for key, value in {
          "GREEN_API_URL": self.green_api_url,
          "GREEN_API_INSTANCE_ID": self.green_api_instance_id,
          "GREEN_API_TOKEN": self.green_api_token,
        }.items()
        if not value
      ]
      if missing:
        raise ConfigurationError(f"Missing Green API configuration: {', '.join(missing)}")

    if not self.google_service_account_json and not self.google_application_credentials:
      raise ConfigurationError(
        "Provide GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS for Google Sheets access."
      )

    has_dashboard_username = bool(self.dashboard_username)
    has_dashboard_password = bool(self.dashboard_password)
    if has_dashboard_username != has_dashboard_password:
      raise ConfigurationError("Set both DASHBOARD_USERNAME and DASHBOARD_PASSWORD or neither.")

    if self.dashboard_auth_enabled and not (has_dashboard_username and has_dashboard_password):
      raise ConfigurationError("Missing dashboard authentication configuration: DASHBOARD_USERNAME, DASHBOARD_PASSWORD")

    return self
