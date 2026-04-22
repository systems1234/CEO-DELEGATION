// WhatsApp provider wrapper

/**
 * Sends a plain text WhatsApp message to a single recipient.
 * @param {string} toNumber  E.164 without '+' (for example "919876543210")
 * @param {string} body      Message text
 * @returns {string}         Provider message ID on success
 * @throws {Error}           On API failure or network error
 */
function waSendText(toNumber, body) {
  _validateWaNumber(toNumber);
  if (!body || body.trim().length === 0) {
    throw new Error("waSendText: body cannot be empty");
  }

  const provider = getWaProvider();

  if (provider === CFG.WA_PROVIDER.META) {
    return _sendTextViaMeta(toNumber, body);
  }

  if (provider === CFG.WA_PROVIDER.GREEN_API) {
    return _sendTextViaGreenApi(toNumber, body);
  }

  throw new Error(`waSendText: unsupported provider "${provider}"`);
}

function _sendTextViaMeta(toNumber, body) {
  const phoneId = getProp("WA_PHONE_NUMBER_ID");
  const token = getProp("WA_ACCESS_TOKEN");
  const url = `${CFG.WA_API_BASE}/${phoneId}/messages`;

  const payload = {
    messaging_product: "whatsapp",
    to: toNumber,
    type: "text",
    text: { body: body.substring(0, 4096) },
  };

  const response = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    headers: { Authorization: `Bearer ${token}` },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  const code = response.getResponseCode();
  const raw = response.getContentText();

  if (code < 200 || code >= 300) {
    Logger.log(`waSendText(meta): ERROR to=${toNumber} code=${code} body=${raw}`);
    throw new Error(`WhatsApp API error ${code}: ${raw}`);
  }

  const parsed = JSON.parse(raw);
  const msgId = parsed?.messages?.[0]?.id ?? "unknown";
  Logger.log(`waSendText(meta): OK to=${toNumber} msgId=${msgId}`);
  return msgId;
}

function _sendTextViaGreenApi(toNumber, body) {
  const apiUrl = _normaliseGreenApiUrl(getProp("GREEN_API_URL"));
  const instanceId = getProp("GREEN_API_INSTANCE_ID");
  const token = getProp("GREEN_API_TOKEN");
  const url = `${apiUrl}/waInstance${encodeURIComponent(instanceId)}/sendMessage/${encodeURIComponent(token)}`;

  const payload = {
    chatId: _toGreenApiChatId(toNumber),
    message: body.substring(0, 20000),
  };

  const response = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  const code = response.getResponseCode();
  const raw = response.getContentText();

  if (code < 200 || code >= 300) {
    Logger.log(`waSendText(greenapi): ERROR to=${toNumber} code=${code} body=${raw}`);
    throw new Error(`Green API error ${code}: ${raw}`);
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    Logger.log(`waSendText(greenapi): invalid JSON response body=${raw}`);
    throw new Error(`Green API returned invalid JSON: ${err.message}`);
  }

  const msgId = parsed?.idMessage ?? "unknown";
  Logger.log(`waSendText(greenapi): OK to=${toNumber} msgId=${msgId}`);
  return msgId;
}

/**
 * Notifies the CEO with a formatted update message.
 * @param {string} message
 */
function notifyCeo(message) {
  const ceoNumber = getProp("CEO_WA_NUMBER");
  waSendText(ceoNumber, `Task Update\n\n${message}`);
}

/**
 * Sends a due-date follow-up DM to an assignee.
 * @param {string} assigneeName
 * @param {string} assigneeNumber
 * @param {string} taskDescription
 * @param {string} rowId
 */
function sendFollowUp(assigneeName, assigneeNumber, taskDescription, rowId) {
  const msg =
    `Hi ${assigneeName}\n\n` +
    `Your task is due today:\n*${taskDescription}*\n\n` +
    `Please reply:\n` +
    `*Done* - if completed\n` +
    `*Postpone* - if you need more time\n\n` +
    `(Reference: ${rowId})`;

  waSendText(assigneeNumber, msg);
  Logger.log(`sendFollowUp -> ${assigneeName} (${assigneeNumber}) rowId=${rowId}`);
}

/**
 * Asks assignee for a new date and reason after they replied "postpone".
 * @param {string} assigneeName
 * @param {string} assigneeNumber
 * @param {string} taskDescription
 * @param {string} rowId
 */
function sendPostponePrompt(assigneeName, assigneeNumber, taskDescription, rowId) {
  const msg =
    `Hi ${assigneeName}, understood.\n\n` +
    `For task: *${taskDescription}*\n\n` +
    `Please reply in this format:\n` +
    `*NEW DATE: DD/MM/YYYY | REASON: <your reason>*\n\n` +
    `(Reference: ${rowId})`;

  waSendText(assigneeNumber, msg);
}

function _validateWaNumber(num) {
  if (!num || !/^\d{10,15}$/.test(num)) {
    throw new Error(`Invalid WhatsApp number format: "${num}" - must be E.164 digits only`);
  }
}

function _toGreenApiChatId(num) {
  return `${num}@c.us`;
}

function _normaliseGreenApiUrl(rawUrl) {
  return String(rawUrl).trim().replace(/\/+$/, "");
}
