// Main webhook and orchestration
//
// Deploy as a Web App in Apps Script.
// Script Properties:
//   OPENAI_API_KEY
//   CEO_WA_NUMBER
//   WA_PROVIDER=meta|greenapi
//   Meta: WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID, WA_VERIFY_TOKEN
//   Green API: GREEN_API_URL, GREEN_API_INSTANCE_ID, GREEN_API_TOKEN

function doGet(e) {
  const provider = getWaProvider();
  if (provider !== CFG.WA_PROVIDER.META) {
    Logger.log(`doGet: provider=${provider} returning OK`);
    return _ok();
  }

  const params = e?.parameter ?? {};
  const mode = params["hub.mode"];
  const token = params["hub.verify_token"];
  const challenge = params["hub.challenge"];
  const verifyToken = getProp("WA_VERIFY_TOKEN");

  if (mode === "subscribe" && token === verifyToken) {
    Logger.log("Webhook verification: OK");
    return ContentService.createTextOutput(challenge);
  }

  Logger.log(`Webhook verification: FAILED mode=${mode} token=${token}`);
  return ContentService.createTextOutput("Forbidden").setMimeType(ContentService.MimeType.TEXT);
}

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e?.postData?.contents ?? "{}");
  } catch (parseErr) {
    Logger.log(`doPost: JSON parse error: ${parseErr.message}`);
    return _ok();
  }

  try {
    const incoming = _extractIncomingMessage(body);
    if (!incoming) return _ok();

    const from = incoming.from;
    const text = incoming.text;
    const ceoNum = getProp("CEO_WA_NUMBER");

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

function _extractIncomingMessage(body) {
  const provider = getWaProvider();

  if (provider === CFG.WA_PROVIDER.META) {
    return _extractMetaIncomingMessage(body);
  }

  if (provider === CFG.WA_PROVIDER.GREEN_API) {
    return _extractGreenApiIncomingMessage(body);
  }

  throw new Error(`Unsupported WhatsApp provider: ${provider}`);
}

function _extractMetaIncomingMessage(body) {
  const entry = body?.entry?.[0];
  const changes = entry?.changes?.[0];
  const value = changes?.value;
  const messages = value?.messages;

  if (!messages || messages.length === 0) return null;

  const msg = messages[0];
  const msgType = msg?.type;
  const text = msgType === "text" ? (msg.text?.body ?? "").trim() : "";
  const from = _normaliseIncomingWaNumber(msg?.from);

  if (!text) {
    Logger.log(`_extractMetaIncomingMessage: ignoring message type=${msgType}`);
    return null;
  }

  if (!from) {
    Logger.log("_extractMetaIncomingMessage: missing sender number");
    return null;
  }

  return { from, text };
}

function _extractGreenApiIncomingMessage(body) {
  const typeWebhook = String(body?.typeWebhook ?? "");
  if (typeWebhook !== "incomingMessageReceived") {
    Logger.log(`_extractGreenApiIncomingMessage: ignoring webhook type=${typeWebhook}`);
    return null;
  }

  const messageType = String(body?.messageData?.typeMessage ?? "");
  let text = "";

  if (messageType === "textMessage") {
    text = body?.messageData?.textMessageData?.textMessage ?? "";
  } else if (messageType === "quotedMessage" || messageType === "extendedTextMessage") {
    text = body?.messageData?.extendedTextMessageData?.text ?? "";
  } else {
    Logger.log(`_extractGreenApiIncomingMessage: ignoring message type=${messageType}`);
    return null;
  }

  const from = _normaliseIncomingWaNumber(body?.senderData?.sender ?? body?.senderData?.chatId);
  const trimmedText = String(text).trim();

  if (!trimmedText) {
    Logger.log(`_extractGreenApiIncomingMessage: empty text for type=${messageType}`);
    return null;
  }

  if (!from) {
    Logger.log("_extractGreenApiIncomingMessage: missing sender number");
    return null;
  }

  return { from, text: trimmedText };
}

function _handleCeoMessage(text) {
  if (!text) return;

  const members = getTeamMembers();
  const knownNames = members.map((member) => member.name);
  const parsed = parseTaskAssignment(text, knownNames);

  if (!parsed) {
    Logger.log("_handleCeoMessage: not a task assignment, ignoring");
    return;
  }

  Logger.log(`_handleCeoMessage: parsed=${JSON.stringify(parsed)}`);

  const member = getMemberByName(parsed.assignee);
  if (!member) {
    waSendText(
      getProp("CEO_WA_NUMBER"),
      `Couldn't find team member *"${parsed.assignee}"* in the Config sheet.\nPlease add them first or check the spelling.`
    );
    return;
  }

  const rowId = createTask(parsed);
  const dueTxt = parsed.dueDate ? `Due: ${_formatDateForDisplay(parsed.dueDate)}` : "No due date set";

  waSendText(
    getProp("CEO_WA_NUMBER"),
    `Task assigned.\n\nAssignee: ${member.name}\nTask: ${parsed.task}\n${dueTxt}\nRef: ${rowId}`
  );

  waSendText(
    member.number,
    `New Task Assigned\n\n${parsed.task}\n\nDue: ${parsed.dueDate ? _formatDateForDisplay(parsed.dueDate) : "Not specified"}\nRef: ${rowId}`
  );

  Logger.log(`_handleCeoMessage: task created rowId=${rowId} for ${member.name}`);
}

function _handleAssigneeReply(fromNumber, text) {
  if (!text) return;

  const lower = text.toLowerCase();
  const rowId = _extractRowId(text);

  if (_isDoneReply(lower)) {
    if (!rowId) {
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

  const pendingPostpone = _getPendingPostpone(fromNumber);
  if (pendingPostpone) {
    const { newDate, reason } = parsePostponeReply(text);
    const targetRowId = rowId ?? pendingPostpone;

    if (!newDate || !reason) {
      waSendText(
        fromNumber,
        "Couldn't parse your reply. Please use:\n*NEW DATE: DD/MM/YYYY | REASON: <your reason>*"
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

    waSendText(fromNumber, `Got it. Task rescheduled to ${_formatDateForDisplay(newDate)}.`);
    notifyCeo(
      `Task Postponed\n\nAssignee: ${taskRow.assigneeName}\nTask: ${taskRow.task}\nNew date: ${_formatDateForDisplay(newDate)}\nReason: ${reason}\nRef: ${targetRowId}`
    );

    Logger.log(`_handleAssigneeReply: postponed rowId=${targetRowId} newDate=${newDate}`);
    return;
  }

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

    _setPendingPostpone(fromNumber, targetRowId);
    sendPostponePrompt(taskRow.assigneeName, fromNumber, taskRow.task, targetRowId);
    return;
  }

  Logger.log(`_handleAssigneeReply: unrecognised reply from=${fromNumber} text="${text}"`);
}

function _processDoneReply(fromNumber, rowId, taskDesc, assigneeName) {
  markTaskDone(rowId);
  waSendText(fromNumber, "Task marked as Done. Great work.");
  notifyCeo(
    `Task Completed\n\nAssignee: ${assigneeName}\nTask: ${taskDesc}\nCompleted: ${_formatDateForDisplay(_todayIso())}\nRef: ${rowId}`
  );
  Logger.log(`_processDoneReply: rowId=${rowId} marked done`);
}

function _setPendingPostpone(number, rowId) {
  PropertiesService.getScriptProperties().setProperty(`POSTPONE_PENDING_${number}`, rowId);
}

function _getPendingPostpone(number) {
  return PropertiesService.getScriptProperties().getProperty(`POSTPONE_PENDING_${number}`) ?? null;
}

function _clearPendingPostpone(number) {
  PropertiesService.getScriptProperties().deleteProperty(`POSTPONE_PENDING_${number}`);
}

function _isDoneReply(lower) {
  return /\b(done|completed|finish|finished|ho gaya|ho gya|complete|kar diya|kardiya|yes)\b/.test(lower);
}

function _isPostponeReply(lower) {
  return /\b(postpone|delay|extend|nahi|nahin|not done|pending|baad mein|later|reschedule|no)\b/.test(lower);
}

function _extractRowId(text) {
  const match = text.match(/\bT\d{13}[0-9A-F]{4}\b/);
  return match ? match[0] : null;
}

function _normaliseIncomingWaNumber(rawValue) {
  const digits = String(rawValue ?? "").replace(/\D/g, "");
  return digits || null;
}

function _getLatestPendingTaskForNumber(number) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const master = ss.getSheetByName(CFG.MASTER_SHEET);
  if (!master) return null;

  const data = master.getDataRange().getValues();
  const C = CFG.COL;
  const cleanNum = number.replace(/\D/g, "");

  for (let i = data.length - 1; i >= 1; i -= 1) {
    const row = data[i];
    const rowNum = String(row[C.ASSIGNEE_NUMBER - 1]).replace(/\D/g, "");
    const rowStatus = String(row[C.STATUS - 1]);

    if (rowNum === cleanNum && rowStatus !== CFG.STATUS.DONE) {
      return {
        rowId: String(row[C.ROW_ID - 1]),
        task: String(row[C.TASK - 1]),
        assigneeName: String(row[C.ASSIGNEE_NAME - 1]),
      };
    }
  }

  return null;
}

function _formatDateForDisplay(isoDate) {
  if (!isoDate) return "N/A";
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
