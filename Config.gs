// Configuration and script-property helpers
//
// All secrets live in Apps Script Project Properties.
//
// Common keys:
//   OPENAI_API_KEY
//   CEO_WA_NUMBER
//   WA_PROVIDER          - optional; "meta" (default) or "greenapi"
//
// Meta-only keys:
//   WA_ACCESS_TOKEN
//   WA_PHONE_NUMBER_ID
//   WA_VERIFY_TOKEN
//
// Green API-only keys:
//   GREEN_API_URL
//   GREEN_API_INSTANCE_ID
//   GREEN_API_TOKEN

const CFG = {
  // Sheet names
  MASTER_SHEET: "Master",
  CONFIG_SHEET: "Config",

  WA_PROVIDER: {
    META: "meta",
    GREEN_API: "greenapi",
  },

  // Master sheet column indices (1-based)
  COL: {
    ASSIGNEE_NAME: 1,
    ASSIGNEE_NUMBER: 2,
    TASK: 3,
    ASSIGN_DATE: 4,
    DUE_DATE: 5,
    NEW_DATE: 6,
    POSTPONE_REASON: 7,
    COMPLETION_DATE: 8,
    STATUS: 9,
    CEO_COMMENTS: 10,
    OTHER: 11,
    ROW_ID: 12,
  },

  // Individual sheet column indices (same schema, no assignee columns)
  IND_COL: {
    TASK: 1,
    ASSIGN_DATE: 2,
    DUE_DATE: 3,
    NEW_DATE: 4,
    POSTPONE_REASON: 5,
    COMPLETION_DATE: 6,
    STATUS: 7,
    CEO_COMMENTS: 8,
    OTHER: 9,
    ROW_ID: 10,
  },

  STATUS: {
    PENDING: "Pending",
    DONE: "Done",
    POSTPONED: "Postponed",
    OVERDUE: "Overdue",
  },

  WA_API_BASE: "https://graph.facebook.com/v19.0",

  OPENAI_MODEL: "gpt-4o",
  OPENAI_URL: "https://api.openai.com/v1/chat/completions",
};

/**
 * Safely retrieves a required script property.
 * @param {string} key
 * @returns {string}
 */
function getProp(key) {
  const val = PropertiesService.getScriptProperties().getProperty(key);
  if (!val) throw new Error(`Missing script property: ${key}`);
  return val;
}

/**
 * Retrieves a script property or falls back to the provided default value.
 * @param {string} key
 * @param {string} defaultValue
 * @returns {string}
 */
function getOptionalProp(key, defaultValue) {
  const val = PropertiesService.getScriptProperties().getProperty(key);
  return val ? val : defaultValue;
}

/**
 * Returns the configured WhatsApp provider.
 * Defaults to Meta to preserve existing deployments.
 * @returns {"meta"|"greenapi"}
 */
function getWaProvider() {
  const rawProvider = String(getOptionalProp("WA_PROVIDER", CFG.WA_PROVIDER.META)).trim().toLowerCase();

  if (rawProvider === CFG.WA_PROVIDER.META) {
    return CFG.WA_PROVIDER.META;
  }

  if (["green", "greenapi", "green-api", "green_api"].includes(rawProvider)) {
    return CFG.WA_PROVIDER.GREEN_API;
  }

  throw new Error(
    `Invalid script property WA_PROVIDER: "${rawProvider}". Expected "meta" or "greenapi".`
  );
}
