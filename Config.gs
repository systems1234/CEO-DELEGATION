// ─── CONFIG ──────────────────────────────────────────────────────────────────
// All secrets live in Apps Script Project Properties (File → Project properties → Script properties)
// Keys required:
//   OPENAI_API_KEY        — for NLP task parsing
//   WA_ACCESS_TOKEN       — Meta WhatsApp Cloud API permanent token
//   WA_PHONE_NUMBER_ID    — Meta phone number ID (from Meta developer dashboard)
//   CEO_WA_NUMBER         — CEO's WhatsApp number in E.164 (e.g. 919876543210)
//   WA_VERIFY_TOKEN       — any random string you choose, same as in Meta webhook config

const CFG = {
  // Sheet names
  MASTER_SHEET: "Master",
  CONFIG_SHEET: "Config",

  // Master sheet column indices (1-based)
  COL: {
    ASSIGNEE_NAME:     1,
    ASSIGNEE_NUMBER:   2,
    TASK:              3,
    ASSIGN_DATE:       4,
    DUE_DATE:          5,
    NEW_DATE:          6,
    POSTPONE_REASON:   7,
    COMPLETION_DATE:   8,
    STATUS:            9,
    CEO_COMMENTS:      10,
    OTHER:             11,
    ROW_ID:            12,   // unique ID per task row for webhook reply matching
  },

  // Individual sheet column indices (same schema, no assignee columns)
  IND_COL: {
    TASK:              1,
    ASSIGN_DATE:       2,
    DUE_DATE:          3,
    NEW_DATE:          4,
    POSTPONE_REASON:   5,
    COMPLETION_DATE:   6,
    STATUS:            7,
    CEO_COMMENTS:      8,
    OTHER:             9,
    ROW_ID:            10,
  },

  // Task statuses
  STATUS: {
    PENDING:    "Pending",
    DONE:       "Done",
    POSTPONED:  "Postponed",
    OVERDUE:    "Overdue",
  },

  // WhatsApp Cloud API base URL
  WA_API_BASE: "https://graph.facebook.com/v19.0",

  // OpenAI
  OPENAI_MODEL: "gpt-4o",
  OPENAI_URL:   "https://api.openai.com/v1/chat/completions",
};

/**
 * Safely retrieves a script property, throws clearly if missing.
 * @param {string} key
 * @returns {string}
 */
function getProp(key) {
  const val = PropertiesService.getScriptProperties().getProperty(key);
  if (!val) throw new Error(`Missing script property: ${key}`);
  return val;
}
