// ─── SHEET OPERATIONS ────────────────────────────────────────────────────────

/**
 * Initialises the spreadsheet on first run:
 *   - Creates Master sheet with headers
 *   - Creates Config sheet with headers
 * Safe to re-run — skips sheets that already exist.
 */
function initSpreadsheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  _ensureMasterSheet(ss);
  _ensureConfigSheet(ss);
  Logger.log("initSpreadsheet: done");
}

/**
 * Returns all rows from the Config sheet as an array of { name, number, sheetName }.
 * @returns {{ name: string, number: string, sheetName: string }[]}
 */
function getTeamMembers() {
  const ss     = SpreadsheetApp.getActiveSpreadsheet();
  const config = ss.getSheetByName(CFG.CONFIG_SHEET);
  if (!config) throw new Error("Config sheet not found — run initSpreadsheet() first");

  const data = config.getDataRange().getValues();
  // Skip header row
  return data.slice(1)
    .filter(row => row[0] && row[1])
    .map(row => ({
      name:      String(row[0]).trim(),
      number:    String(row[1]).trim().replace(/\D/g, ""),  // digits only
      sheetName: String(row[2] || row[0]).trim(),
    }));
}

/**
 * Looks up a team member by name (case-insensitive exact match).
 * @param {string} name
 * @returns {{ name: string, number: string, sheetName: string } | null}
 */
function getMemberByName(name) {
  if (!name) return null;
  const lower   = name.toLowerCase().trim();
  const members = getTeamMembers();
  return members.find(m => m.name.toLowerCase() === lower) ?? null;
}

/**
 * Writes a new task to both the Master sheet and the assignee's individual sheet.
 * Returns the generated rowId.
 *
 * @param {{ assignee: string, task: string, dueDate: string }} parsed
 * @returns {string} rowId
 */
function createTask(parsed) {
  const member = getMemberByName(parsed.assignee);
  if (!member) throw new Error(`createTask: unknown assignee "${parsed.assignee}"`);

  const ss      = SpreadsheetApp.getActiveSpreadsheet();
  const today   = _todayIso();
  const rowId   = _generateRowId();
  const dueDate = parsed.dueDate ?? "";

  // ── Master sheet ──────────────────────────────────────────────────────────
  const master = _getOrCreateSheet(ss, CFG.MASTER_SHEET);
  master.appendRow([
    member.name,            // COL 1 - assignee name
    member.number,          // COL 2 - assignee number
    parsed.task,            // COL 3 - task
    today,                  // COL 4 - assign date
    dueDate,                // COL 5 - due date
    "",                     // COL 6 - new date
    "",                     // COL 7 - postpone reason
    "",                     // COL 8 - completion date
    CFG.STATUS.PENDING,     // COL 9 - status
    "",                     // COL 10 - CEO comments
    "",                     // COL 11 - other
    rowId,                  // COL 12 - row ID
  ]);
  _applyMasterFormatting(master);

  // ── Individual sheet ──────────────────────────────────────────────────────
  const indSheet = _getOrCreateSheet(ss, member.sheetName);
  _ensureIndividualHeaders(indSheet, member.name);
  indSheet.appendRow([
    parsed.task,            // COL 1 - task
    today,                  // COL 2 - assign date
    dueDate,                // COL 3 - due date
    "",                     // COL 4 - new date
    "",                     // COL 5 - postpone reason
    "",                     // COL 6 - completion date
    CFG.STATUS.PENDING,     // COL 7 - status
    "",                     // COL 8 - CEO comments
    "",                     // COL 9 - other
    rowId,                  // COL 10 - row ID
  ]);

  Logger.log(`createTask: rowId=${rowId} assignee=${member.name} due=${dueDate}`);
  return rowId;
}

/**
 * Marks a task as Done in both Master and individual sheets.
 * @param {string} rowId
 */
function markTaskDone(rowId) {
  _updateTaskFields(rowId, {
    [CFG.COL.STATUS]:          CFG.STATUS.DONE,
    [CFG.COL.COMPLETION_DATE]: _todayIso(),
  }, {
    [CFG.IND_COL.STATUS]:          CFG.STATUS.DONE,
    [CFG.IND_COL.COMPLETION_DATE]: _todayIso(),
  });
  Logger.log(`markTaskDone: rowId=${rowId}`);
}

/**
 * Updates a task with a new due date and postpone reason.
 * @param {string} rowId
 * @param {string} newDate   YYYY-MM-DD
 * @param {string} reason
 */
function postponeTask(rowId, newDate, reason) {
  _updateTaskFields(rowId, {
    [CFG.COL.STATUS]:          CFG.STATUS.POSTPONED,
    [CFG.COL.NEW_DATE]:        newDate,
    [CFG.COL.POSTPONE_REASON]: reason,
  }, {
    [CFG.IND_COL.STATUS]:          CFG.STATUS.POSTPONED,
    [CFG.IND_COL.NEW_DATE]:        newDate,
    [CFG.IND_COL.POSTPONE_REASON]: reason,
  });
  Logger.log(`postponeTask: rowId=${rowId} newDate=${newDate}`);
}

/**
 * Returns all tasks that are due today and still Pending (or Postponed with today as new date).
 * @returns {{ rowId, assigneeName, assigneeNumber, task, dueDate, status }[]}
 */
function getTasksDueToday() {
  const ss     = SpreadsheetApp.getActiveSpreadsheet();
  const master = ss.getSheetByName(CFG.MASTER_SHEET);
  if (!master) return [];

  const today = _todayIso();
  const data  = master.getDataRange().getValues();
  const C     = CFG.COL;

  return data.slice(1)  // skip header
    .filter(row => {
      const status  = String(row[C.STATUS - 1]).trim();
      const due     = _normaliseDateCell(row[C.DUE_DATE - 1]);
      const newDate = _normaliseDateCell(row[C.NEW_DATE - 1]);

      if (status === CFG.STATUS.DONE) return false;

      const isDueToday  = due === today && status === CFG.STATUS.PENDING;
      const isNewToday  = newDate === today && status === CFG.STATUS.POSTPONED;
      return isDueToday || isNewToday;
    })
    .map(row => ({
      rowId:          String(row[C.ROW_ID - 1]),
      assigneeName:   String(row[C.ASSIGNEE_NAME - 1]),
      assigneeNumber: String(row[C.ASSIGNEE_NUMBER - 1]),
      task:           String(row[C.TASK - 1]),
      dueDate:        _normaliseDateCell(row[C.DUE_DATE - 1]),
      status:         String(row[C.STATUS - 1]),
    }));
}

/**
 * Fetches a single task row by rowId from the Master sheet.
 * @param {string} rowId
 * @returns {Object|null}
 */
function getTaskByRowId(rowId) {
  const ss     = SpreadsheetApp.getActiveSpreadsheet();
  const master = ss.getSheetByName(CFG.MASTER_SHEET);
  if (!master) return null;

  const data = master.getDataRange().getValues();
  const C    = CFG.COL;

  const row = data.slice(1).find(r => String(r[C.ROW_ID - 1]) === rowId);
  if (!row) return null;

  return {
    rowId:          String(row[C.ROW_ID - 1]),
    assigneeName:   String(row[C.ASSIGNEE_NAME - 1]),
    assigneeNumber: String(row[C.ASSIGNEE_NUMBER - 1]),
    task:           String(row[C.TASK - 1]),
    dueDate:        _normaliseDateCell(row[C.DUE_DATE - 1]),
    status:         String(row[C.STATUS - 1]),
  };
}

// ─── PRIVATE HELPERS ─────────────────────────────────────────────────────────

function _updateTaskFields(rowId, masterFieldMap, indFieldMap) {
  const ss     = SpreadsheetApp.getActiveSpreadsheet();
  const master = ss.getSheetByName(CFG.MASTER_SHEET);
  if (!master) throw new Error("Master sheet not found");

  const data   = master.getDataRange().getValues();
  const C      = CFG.COL;

  let assigneeSheetName = null;

  for (let i = 1; i < data.length; i++) {
    if (String(data[i][C.ROW_ID - 1]) !== rowId) continue;

    assigneeSheetName = String(data[i][C.ASSIGNEE_NAME - 1]);

    // Update master
    for (const [colIdx, val] of Object.entries(masterFieldMap)) {
      master.getRange(i + 1, Number(colIdx)).setValue(val);
    }
    break;
  }

  if (!assigneeSheetName) {
    Logger.log(`_updateTaskFields: rowId=${rowId} not found in master`);
    return;
  }

  // Update individual sheet
  const member   = getMemberByName(assigneeSheetName);
  const indSheet = member ? ss.getSheetByName(member.sheetName) : null;
  if (!indSheet) return;

  const indData = indSheet.getDataRange().getValues();
  const IC      = CFG.IND_COL;

  for (let i = 1; i < indData.length; i++) {
    if (String(indData[i][IC.ROW_ID - 1]) !== rowId) continue;

    for (const [colIdx, val] of Object.entries(indFieldMap)) {
      indSheet.getRange(i + 1, Number(colIdx)).setValue(val);
    }
    break;
  }
}

function _getOrCreateSheet(ss, name) {
  return ss.getSheetByName(name) ?? ss.insertSheet(name);
}

function _ensureMasterSheet(ss) {
  const sheet = _getOrCreateSheet(ss, CFG.MASTER_SHEET);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow([
      "Assignee Name", "Assignee Number", "Task",
      "Assign Date", "Due Date", "New Date",
      "Postpone Reason", "Completion Date", "Status",
      "CEO Comments", "Other", "Row ID",
    ]);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, 12).setFontWeight("bold").setBackground("#4A90D9").setFontColor("#FFFFFF");
  }
}

function _ensureConfigSheet(ss) {
  const sheet = _getOrCreateSheet(ss, CFG.CONFIG_SHEET);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(["Name", "WhatsApp Number (E.164 digits only)", "Sheet Name (optional)"]);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, 3).setFontWeight("bold").setBackground("#34A853").setFontColor("#FFFFFF");
    sheet.getRange("A2").setNote("Enter member names exactly as CEO refers to them in messages");
    sheet.getRange("B2").setNote("Digits only, no + or spaces. E.g. 919876543210");
  }
}

function _ensureIndividualHeaders(sheet, memberName) {
  if (sheet.getLastRow() === 0) {
    sheet.appendRow([
      "Task", "Assign Date", "Due Date",
      "New Date", "Postpone Reason", "Completion Date",
      "Status", "CEO Comments", "Other", "Row ID",
    ]);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, 10).setFontWeight("bold").setBackground("#F4B400").setFontColor("#FFFFFF");
    sheet.setName(memberName);
  }
}

function _applyMasterFormatting(sheet) {
  // Auto-resize columns for readability
  for (let c = 1; c <= 12; c++) {
    sheet.autoResizeColumn(c);
  }
}

function _generateRowId() {
  // Timestamp + 4 random hex chars → collision-proof for 10 members
  return `T${Date.now()}${Math.random().toString(16).slice(2, 6).toUpperCase()}`;
}

function _normaliseDateCell(cell) {
  if (!cell) return "";
  if (cell instanceof Date) {
    return Utilities.formatDate(cell, "Asia/Kolkata", "yyyy-MM-dd");
  }
  // Already a string
  const s = String(cell).trim();
  // Handle DD/MM/YYYY
  const m = s.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$/);
  if (m) {
    let [, d, mo, y] = m.map(Number);
    if (y < 100) y += 2000;
    return `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }
  return s;
}
