-- CEO Delegation BigQuery schema.
-- Applied idempotently by GoogleBigQueryRepository.ensure_schema() on startup,
-- and can also be run by hand against a fresh project.

CREATE SCHEMA IF NOT EXISTS `{project}.{dataset}` OPTIONS (location = 'US');

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.team_members` (
  name STRING NOT NULL,
  number STRING NOT NULL,
  email STRING,
  created_at TIMESTAMP NOT NULL
);

ALTER TABLE `{project}.{dataset}.team_members` ADD COLUMN IF NOT EXISTS email STRING;

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.tasks` (
  row_id STRING NOT NULL,
  assignee_name STRING NOT NULL,
  assignee_number STRING NOT NULL,
  task STRING NOT NULL,
  priority STRING,
  assign_date DATE,
  due_date DATE,
  new_date DATE,
  postpone_reason STRING,
  completion_date DATE,
  status STRING NOT NULL,
  ceo_comments STRING,
  other STRING,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);

ALTER TABLE `{project}.{dataset}.tasks` ADD COLUMN IF NOT EXISTS priority STRING;

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.runtime_state` (
  key STRING NOT NULL,
  value STRING NOT NULL,
  updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.chat_log` (
  timestamp TIMESTAMP NOT NULL,
  direction STRING NOT NULL,
  number STRING NOT NULL,
  name STRING,
  message STRING
);
