// ─── MAIN WEBHOOK + ORCHESTRATION ────────────────────────────────────────────
//
// Deploy as: Extensions → Apps Script → Deploy → New deployment → Web App
//   Execute as: Me
//   Who has access: Anyone
// Paste the deployment URL into Meta's WhatsApp webhook config.
//
// Script Properties required (File → Project properties → Script properties):
//   OPENAI_API_KEY, WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID, CEO_WA_NUMBER, WA_VERIFY_TOKEN

// ─── WEBHOOK VERIFICATION (GET) ──────────────────────────────────────────────

function doGet(e) {
  const params      = e?.parameter ?? {};
  const mode        = params["hub.mode"];
  const token       = params["hub.verify_token"];
  const challenge   = params["hub.challenge"];
  const verifyToken = getProp("WA_VERIFY_TOKEN");

  if (mode === "subscribe" && token === verifyToken) {
    Logger.log("Webhook verification: OK");
    return ContentService.createTextOutput(challenge);
  }

  Logger.log(`Webhook verification: FAILED mode=${mode} token=${token}`);
  return ContentService.createTextOutput("Forbidden").setMimeType(ContentService.MimeType.TEXT);
}

// ─── INCOMING MESSAGES (POST) ────────────────────────────────────────────────

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e?.postData?.contents ?? "{}");
  } catch (parseErr) {
    Logger.log(`doPost: JSON parse error: ${parseErr.message}`);
    return _ok();
  }

  try {
    const entry   = body?.entry?.[0];
    const changes = entry?.changes?.[0];
    const value   = changes?.value;
    const messages = value?.messages;

    if (!messages || messages.length === 0) return _ok();

    const msg     = messages[0];
    const from    = msg.from;              // E.164 digits, no +
    const msgType = msg.type;
    const text    = msgType === "text" ? (msg.text?.body ?? "").trim() : "";
    const ceoNum  = getProp("CEO_WA_NUMBER");

    Logger.log(`doPost: from=${from} text="${text}"`);

    if (from === ceoNum) {
      _handleCeoMessage(text);
    } else {
      _handleAssigneeReply(from, text);
    }
  } catch (err) {
    Logger.log(`doPost: unhandled error: ${err.message}\n${err.stack}`);
  }

  return _ok();
}

// ─── CEO MESSAGE HANDLER ─────────────────────────────────────────────────────

/**
 * Processes a message sent by the CEO.
 * Determines if it's a task assignment and creates the task if so.
 * @param {string} text
 */
function _handleCeoMessage(text) {
  if (!text) return;

  const members    = getTeamMembers();
  const knownNames = members.map(m => m.name);
  const parsed     = parseTaskAssignment(text, knownNames);

  if (!parsed) {
    Logger.log("_handleCeoMessage: not a task assignment, ignoring");
    return;
  }

  Logger.log(`_handleCeoMessage: parsed=${JSON.stringify(parsed)}`);

  const member = getMemberByName(parsed.assignee);
  if (!member) {
    const ceoNum = getProp("CEO_WA_NUMBER");
    waSendText(ceoNum,
      `⚠️ Couldn't find team member *"${parsed.assignee}"* in the Config sheet.\n` +
      `Please add them first or check the spelling.`
    );
    return;
  }

  const rowId = createTask(parsed);

  // Confirm to CEO
  const dueTxt = parsed.dueDate
    ? `Due: ${_formatDateForDisplay(parsed.dueDate)}`
    : "No due date set";
  waSendText(getProp("CEO_WA_NUMBER"),
    `✅ Task assigned!\n\n` +
    `👤 ${member.name}\n` +
    `📝 ${parsed.task}\n` +
    `📅 ${dueTxt}\n` +
    `🔖 Ref: ${rowId}`
  );

  // Notify assignee
  waSendText(member.number,
    `📋 *New Task Assigned*\n\n` +
    `${parsed.task}\n\n` +
    `📅 Due: ${parsed.dueDate ? _formatDateForDisplay(parsed.dueDate) : "Not specified"}\n` +
    `🔖 Ref: ${rowId}`
  );

  Logger.log(`_handleCeoMessage: task created rowId=${rowId} for ${member.name}`);
}

// ─── ASSIGNEE REPLY HANDLER ───────────────────────────────────────────────────

/**
 * Handles a reply from a team member (Done / Postpone / Postpone details).
 * @param {string} fromNumber  E.164 digits
 * @param {string} text
 */
function _handleAssigneeReply(fromNumber, text) {
  if (!text) return;

  const lower  = text.toLowerCase();
  const rowId  = _extractRowId(text);

  // ── DONE reply ───────────────────────────────────────────────────────────
  if (_isDoneReply(lower)) {
    if (!rowId) {
      // If no rowId, try to find the latest pending task for this person
      const taskRow = _getLatestPendingTaskForNumber(fromNumber);
      if (!taskRow) {
        waSendText(fromNumber, "Couldn't find your task. Please include the Reference ID in your reply.");
        return;
      }
      _processDoneReply(fromNumber, taskRow.rowId, taskRow.task, taskRow.assigneeName);
      return;
    }
    const taskRow = getTaskByRowId(rowId);
    if (!taskRow) {
      waSendText(fromNumber, `Task with ref ${rowId} not found.`);
      return;
    }
    _processDoneReply(fromNumber, rowId, taskRow.task, taskRow.assigneeName);
    return;
  }

  // ── POSTPONE reply ───────────────────────────────────────────────────────
  if (_isPostponeReply(lower)) {
    const targetRowId = rowId ?? _getLatestPendingTaskForNumber(fromNumber)?.rowId;
    if (!targetRowId) {
      waSendText(fromNumber, "Couldn't find your task. Please include the Reference ID.");
      return;
    }
    const taskRow = getTaskByRowId(targetRowId);
    if (!taskRow) {
      waSendText(fromNumber, `Task with ref ${targetRowId} not found.`);
      return;
    }
    // Store that we're awaiting postpone details for this number+rowId
    _setPendingPostpone(fromNumber, targetRowId);
    sendPostponePrompt(taskRow.assigneeName, fromNumber, taskRow.task, targetRowId);
    return;
  }

  // ── POSTPONE DETAILS (new date + reason) ────────────────────────────────
  const pendingPostpone = _getPendingPostpone(fromNumber);
  if (pendingPostpone) {
    const { newDate, reason } = parsePostponeReply(text);
    const targetRowId = rowId ?? pendingPostpone;

    if (!newDate || !reason) {
      waSendText(fromNumber,
        `Couldn't parse your reply. Please use:\n*NEW DATE: DD/MM/YYYY | REASON: <your reason>*`
      );
      return;
    }

    const taskRow = getTaskByRowId(targetRowId);
    if (!taskRow) {
      waSendText(fromNumber, `Task ref ${targetRowId} not found.`);
      return;
    }

    postponeTask(targetRowId, newDate, reason);
    _clearPendingPostpone(fromNumber);

    waSendText(fromNumber, `✅ Got it! Task rescheduled to ${_formatDateForDisplay(newDate)}.`);
    notifyCeo(
      `📅 *Task Postponed*\n\n` +
      `👤 ${taskRow.assigneeName}\n` +
      `📝 ${taskRow.task}\n` +
      `🗓️ New date: ${_formatDateForDisplay(newDate)}\n` +
      `💬 Reason: ${reason}\n` +
      `🔖 Ref: ${targetRowId}`
    );

    Logger.log(`_handleAssigneeReply: postponed rowId=${targetRowId} newDate=${newDate}`);
    return;
  }

  Logger.log(`_handleAssigneeReply: unrecognised reply from=${fromNumber} text="${text}"`);
}

// ─── DONE REPLY PROCESSOR ────────────────────────────────────────────────────

function _processDoneReply(fromNumber, rowId, taskDesc, assigneeName) {
  markTaskDone(rowId);
  waSendText(fromNumber, `🎉 Task marked as Done! Great work.`);
  notifyCeo(
    `✅ *Task Completed*\n\n` +
    `👤 ${assigneeName}\n` +
    `📝 ${taskDesc}\n` +
    `📅 Completed: ${_formatDateForDisplay(_todayIso())}\n` +
    `🔖 Ref: ${rowId}`
  );
  Logger.log(`_processDoneReply: rowId=${rowId} marked done`);
}

// ─── PENDING POSTPONE STATE ──────────────────────────────────────────────────
// Uses ScriptProperties to store "awaiting postpone details" state per number.
// Key: POSTPONE_PENDING_<number>, Value: rowId

function _setPendingPostpone(number, rowId) {
  PropertiesService.getScriptProperties().setProperty(`POSTPONE_PENDING_${number}`, rowId);
}

function _getPendingPostpone(number) {
  return PropertiesService.getScriptProperties().getProperty(`POSTPONE_PENDING_${number}`) ?? null;
}

function _clearPendingPostpone(number) {
  PropertiesService.getScriptProperties().deleteProperty(`POSTPONE_PENDING_${number}`);
}

// ─── HELPERS ─────────────────────────────────────────────────────────────────

function _isDoneReply(lower) {
  return /\b(done|completed|finish|finished|ho gaya|ho gya|complete|kar diya|kardiya|yes)\b/.test(lower);
}

function _isPostponeReply(lower) {
  return /\b(postpone|delay|extend|nahi|nahin|not done|pending|baad mein|later|reschedule|no)\b/.test(lower);
}

/**
 * Extracts a row ID (format: T<digits><4hex>) from message text.
 * @param {string} text
 * @returns {string|null}
 */
function _extractRowId(text) {
  const match = text.match(/\bT\d{13}[0-9A-F]{4}\b/);
  return match ? match[0] : null;
}

/**
 * Returns the most recent Pending task for a given WhatsApp number from Master sheet.
 * @param {string} number
 * @returns {{rowId, task, assigneeName}|null}
 */
function _getLatestPendingTaskForNumber(number) {
  const ss     = SpreadsheetApp.getActiveSpreadsheet();
  const master = ss.getSheetByName(CFG.MASTER_SHEET);
  if (!master) return null;

  const data     = master.getDataRange().getValues();
  const C        = CFG.COL;
  const cleanNum = number.replace(/\D/g, "");

  // Iterate in reverse to get most recent first
  for (let i = data.length - 1; i >= 1; i--) {
    const row       = data[i];
    const rowNum    = String(row[C.ASSIGNEE_NUMBER - 1]).replace(/\D/g, "");
    const rowStatus = String(row[C.STATUS - 1]);

    if (rowNum === cleanNum && rowStatus !== CFG.STATUS.DONE) {
      return {
        rowId:        String(row[C.ROW_ID - 1]),
        task:         String(row[C.TASK - 1]),
        assigneeName: String(row[C.ASSIGNEE_NAME - 1]),
      };
    }
  }
  return null;
}

function _formatDateForDisplay(isoDate) {
  if (!isoDate) return "N/A";
  // YYYY-MM-DD → DD/MM/YYYY
  const parts = isoDate.split("-");
  if (parts.length !== 3) return isoDate;
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

function _todayIso() {
  return Utilities.formatDate(new Date(), "Asia/Kolkata", "yyyy-MM-dd");
}

function _ok() {
  return ContentService.createTextOutput("OK").setMimeType(ContentService.MimeType.TEXT);
}
