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

-- Department-task delegation architecture (v2).
-- The LLM/WhatsApp pipeline posts directly into All_Tasks (department-level,
-- no individual owner yet). A TL/Manager assigns it to one doer at a time by
-- inserting a new Department_Tasks row; reassigning inserts another row and
-- the previous one stays as history (current assignment = latest row_created_at
-- per Task_id). Each assignment gets its own doer_task_id, minted the moment
-- the TL/Manager assigns it (first appearance is the auto-created "In Progress"
-- row in doer_Task_update); doer_Tasks only ever holds completed attempts.

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.All_Tasks` (
  Task_id STRING NOT NULL,
  Created_date_time TIMESTAMP NOT NULL,
  Task_brief STRING NOT NULL,
  Assigned_Department STRING NOT NULL,
  CEO_Task_due_date DATE,
  Priority STRING,
  NY_Task_ID STRING
);

-- NY_Task_ID is the CEO's own reference number for a task, kept separate from
-- Task_id (our system id) so CEO-assigned tasks stay traceable against his
-- own tracking. Not every task has one.
ALTER TABLE `{project}.{dataset}.All_Tasks` ADD COLUMN IF NOT EXISTS NY_Task_ID STRING;

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.Department_Tasks` (
  Task_id STRING NOT NULL,
  Task_brief STRING NOT NULL,
  comments STRING,
  Department_due_date DATE,
  Priority STRING,
  Assigned_by STRING NOT NULL,
  Assigned_to_Doer STRING NOT NULL,
  doer_task_id STRING NOT NULL,
  row_created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.Task_Revised` (
  Task_id STRING NOT NULL,
  Revised_id STRING NOT NULL,
  Revised_date DATE NOT NULL,
  revised_by STRING NOT NULL,
  Revised_date_time_stamp TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.doer_Tasks` (
  Task_id STRING NOT NULL,
  Task_brief STRING NOT NULL,
  Task_status STRING NOT NULL,
  completion_date DATE,
  Comments STRING,
  Assigned_by STRING NOT NULL,
  doer_task_id STRING NOT NULL,
  submitted_by STRING NOT NULL,
  row_created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.doer_Task_update` (
  Task_id STRING NOT NULL,
  Task_Update_id STRING NOT NULL,
  doer_task_id STRING NOT NULL,
  Task_update STRING NOT NULL,
  Comments STRING,
  submitted_by STRING NOT NULL,
  row_created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.Reporting_to` (
  TL_Name STRING,
  Manager_Name STRING,
  Emp_Name STRING NOT NULL,
  Emp_id STRING NOT NULL,
  TL_Email STRING,
  Manager_Email STRING
);

-- Team lookups (get_team_for) match on TL_Email/Manager_Email against the
-- logged-in user's email, not on TL_Name/Manager_Name — names alone can't
-- disambiguate a login, and a management user's name never resembles their
-- own email.
ALTER TABLE `{project}.{dataset}.Reporting_to` ADD COLUMN IF NOT EXISTS TL_Email STRING;
ALTER TABLE `{project}.{dataset}.Reporting_to` ADD COLUMN IF NOT EXISTS Manager_Email STRING;

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.Users` (
  user_id STRING NOT NULL,
  name STRING NOT NULL,
  role STRING NOT NULL,
  active BOOL NOT NULL,
  created_at TIMESTAMP NOT NULL
);
