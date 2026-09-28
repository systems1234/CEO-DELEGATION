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

  openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
  openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
  openai_api_url: str = Field(
    default="https://api.openai.com/v1/chat/completions",
    alias="OPENAI_API_URL",
  )

  ceo_wa_number: str = Field(default="", alias="CEO_WA_NUMBER")
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

  bigquery_project_id: str = Field(alias="BIGQUERY_PROJECT_ID")
  bigquery_dataset: str = Field(default="ceod", alias="BIGQUERY_DATASET")
  google_service_account_json: str | None = Field(default=None, alias="GOOGLE_SERVICE_ACCOUNT_JSON")
  google_application_credentials: str | None = Field(default=None, alias="GOOGLE_APPLICATION_CREDENTIALS")

  hr_dataset: str = Field(default="gempundit_db", alias="HR_DATASET")
  hr_employee_table: str = Field(default="employee", alias="HR_EMPLOYEE_TABLE")
  sso_project_tag: str = Field(default="ceo_del_sys", alias="SSO_PROJECT_TAG")

  # Off by default: on serverless, running every CREATE/ALTER statement in
  # schema.sql on each cold start is slow enough (BigQuery job submit + poll
  # per statement) to risk hitting the function timeout, and it's a no-op
  # once the schema already exists. Apply schema changes out-of-band instead
  # (locally, or by flipping this on for one deploy) rather than on every request.
  run_schema_migrations: bool = Field(default=False, alias="RUN_SCHEMA_MIGRATIONS")

  script_timezone: str = Field(default="Asia/Kolkata", alias="SCRIPT_TIMEZONE")
  http_timeout_seconds: float = Field(default=20.0, alias="HTTP_TIMEOUT_SECONDS")

  dashboard_auth_enabled: bool = Field(default=True, alias="DASHBOARD_AUTH_ENABLED")
  google_oauth_client_id: str | None = Field(default=None, alias="GOOGLE_OAUTH_CLIENT_ID")
  google_oauth_client_secret: str | None = Field(default=None, alias="GOOGLE_OAUTH_CLIENT_SECRET")
  allowed_google_domain: str | None = Field(default=None, alias="ALLOWED_GOOGLE_DOMAIN")
  allowed_google_emails: str | None = Field(default=None, alias="ALLOWED_GOOGLE_EMAILS")
  session_secret: str | None = Field(default=None, alias="SESSION_SECRET")
  public_base_url: str | None = Field(default=None, alias="PUBLIC_BASE_URL")

  cron_secret: str | None = Field(default=None, alias="CRON_SECRET")

  @model_validator(mode="after")
  def validate_required_fields(self) -> "Settings":
    # WhatsApp (Meta/Green API) and OpenAI credentials are intentionally NOT
    # required here: the department-task dashboard flow (All_Tasks ->
    # Department_Tasks -> doer_Tasks/doer_Task_update) runs entirely without
    # them. The WhatsApp gateway and parser are still constructed, but any
    # attempt to actually send a message will fail at call time (caught and
    # logged, never crashes the app) until real credentials are added.

    if not self.google_service_account_json and not self.google_application_credentials:
      raise ConfigurationError(
        "Provide GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS for BigQuery access."
      )

    if self.dashboard_auth_enabled:
      missing = [
        key
        for key, value in {
          "GOOGLE_OAUTH_CLIENT_ID": self.google_oauth_client_id,
          "GOOGLE_OAUTH_CLIENT_SECRET": self.google_oauth_client_secret,
          "SESSION_SECRET": self.session_secret,
        }.items()
        if not value
      ]
      if missing:
        raise ConfigurationError(f"Missing SSO configuration: {', '.join(missing)}")
      # Login is gated by the HR employee table (Project_id contains sso_project_tag),
      # not by ALLOWED_GOOGLE_DOMAIN/ALLOWED_GOOGLE_EMAILS. Those fields are kept only
      # as an optional manual override and are no longer required.

    return self
