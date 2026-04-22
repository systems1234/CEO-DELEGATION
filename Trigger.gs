// ─── DAILY TRIGGER ───────────────────────────────────────────────────────────
//
// Setup: Run `installDailyTrigger()` ONCE manually from the Apps Script editor.
// This creates a time-based trigger that calls `dailyFollowUpCheck` every day at 9 AM IST.

/**
 * Installs the daily 9 AM IST trigger. Run once manually.
 * Safe to re-run — removes duplicate triggers before installing.
 */
function installDailyTrigger() {
  // Remove any existing triggers for dailyFollowUpCheck to prevent duplicates
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === "dailyFollowUpCheck")
    .forEach(t => ScriptApp.deleteTrigger(t));

  // Apps Script time triggers run in the script timezone.
  // Set your script timezone to Asia/Kolkata in Project Settings first.
  ScriptApp.newTrigger("dailyFollowUpCheck")
    .timeBased()
    .everyDays(1)
    .atHour(9)
    .create();

  Logger.log("installDailyTrigger: installed — fires daily at 9 AM in script timezone");
}

/**
 * Main daily job. Called automatically by the time trigger.
 *   1. Finds all tasks due today → sends WhatsApp follow-up DMs
 *   2. Finds all tasks overdue (past due, still Pending) → marks Overdue + notifies CEO
 */
function dailyFollowUpCheck() {
  Logger.log("dailyFollowUpCheck: starting");

  try {
    _sendDueTodayFollowUps();
  } catch (err) {
    Logger.log(`dailyFollowUpCheck: _sendDueTodayFollowUps failed: ${err.message}`);
  }

  try {
    _markAndNotifyOverdueTasks();
  } catch (err) {
    Logger.log(`dailyFollowUpCheck: _markAndNotifyOverdueTasks failed: ${err.message}`);
  }

  Logger.log("dailyFollowUpCheck: done");
}

// ─── DUE TODAY ───────────────────────────────────────────────────────────────

function _sendDueTodayFollowUps() {
  const dueTasks = getTasksDueToday();

  if (dueTasks.length === 0) {
    Logger.log("_sendDueTodayFollowUps: no tasks due today");
    return;
  }

  Logger.log(`_sendDueTodayFollowUps: ${dueTasks.length} task(s) due today`);

  for (const task of dueTasks) {
    try {
      sendFollowUp(task.assigneeName, task.assigneeNumber, task.task, task.rowId);
      Logger.log(`_sendDueTodayFollowUps: sent to ${task.assigneeName} rowId=${task.rowId}`);
    } catch (err) {
      Logger.log(`_sendDueTodayFollowUps: failed for rowId=${task.rowId}: ${err.message}`);
    }
  }
}

// ─── OVERDUE ─────────────────────────────────────────────────────────────────

function _markAndNotifyOverdueTasks() {
  const ss     = SpreadsheetApp.getActiveSpreadsheet();
  const master = ss.getSheetByName(CFG.MASTER_SHEET);
  if (!master) return;

  const today  = _todayIso();
  const data   = master.getDataRange().getValues();
  const C      = CFG.COL;
  const overdueTasks = [];

  for (let i = 1; i < data.length; i++) {
    const row            = data[i];
    const status         = String(row[C.STATUS - 1]).trim();
    const due            = _normaliseDateCell(row[C.DUE_DATE - 1]);
    const postponedDate  = _normaliseDateCell(row[C.NEW_DATE - 1]);
    const effectiveDue   = _getEffectiveDueDate(status, due, postponedDate);

    // Flag active tasks whose current effective due date is strictly before today.
    if (!_isActiveTaskStatus(status)) continue;
    if (!effectiveDue) continue;
    if (effectiveDue >= today) continue;

    // Mark overdue in master
    master.getRange(i + 1, C.STATUS).setValue(CFG.STATUS.OVERDUE);

    // Mark overdue in individual sheet
    const memberName = String(row[C.ASSIGNEE_NAME - 1]);
    const member     = getMemberByName(memberName);
    if (member) {
      const indSheet = ss.getSheetByName(member.sheetName);
      if (indSheet) {
        const indData = indSheet.getDataRange().getValues();
        const IC      = CFG.IND_COL;
        const rowId   = String(row[C.ROW_ID - 1]);
        for (let j = 1; j < indData.length; j++) {
          if (String(indData[j][IC.ROW_ID - 1]) === rowId) {
            indSheet.getRange(j + 1, IC.STATUS).setValue(CFG.STATUS.OVERDUE);
            break;
          }
        }
      }
    }

    overdueTasks.push({
      assigneeName:   String(row[C.ASSIGNEE_NAME - 1]),
      assigneeNumber: String(row[C.ASSIGNEE_NUMBER - 1]),
      task:           String(row[C.TASK - 1]),
      dueDate:        effectiveDue,
      rowId:          String(row[C.ROW_ID - 1]),
    });
  }

  if (overdueTasks.length === 0) {
    Logger.log("_markAndNotifyOverdueTasks: none");
    return;
  }

  Logger.log(`_markAndNotifyOverdueTasks: ${overdueTasks.length} overdue task(s)`);

  // Send one consolidated overdue summary to CEO
  const lines = overdueTasks.map((t, idx) =>
    `${idx + 1}. *${t.assigneeName}* — ${t.task}\n   Was due: ${_formatDateForDisplay(t.dueDate)} | Ref: ${t.rowId}`
  ).join("\n\n");

  notifyCeo(`🚨 *Overdue Tasks (${overdueTasks.length})*\n\n${lines}`);

  // Also DM each overdue assignee
  for (const t of overdueTasks) {
    try {
      waSendText(t.assigneeNumber,
        `⚠️ Hi ${t.assigneeName}, your task is *overdue*:\n\n` +
        `📝 ${t.task}\n` +
        `Was due: ${_formatDateForDisplay(t.dueDate)}\n\n` +
        `Please reply:\n✅ *Done* — if completed\n📅 *Postpone* — if you need more time\n\n(Ref: ${t.rowId})`
      );
    } catch (err) {
      Logger.log(`_markAndNotifyOverdueTasks: DM failed for ${t.assigneeName}: ${err.message}`);
    }
  }
}

// ─── HELPERS ─────────────────────────────────────────────────────────────────

function _todayIso() {
  return Utilities.formatDate(new Date(), "Asia/Kolkata", "yyyy-MM-dd");
}

function _isActiveTaskStatus(status) {
  return status === CFG.STATUS.PENDING || status === CFG.STATUS.POSTPONED;
}

function _getEffectiveDueDate(status, dueDate, postponedDate) {
  if (status === CFG.STATUS.POSTPONED && postponedDate) {
    return postponedDate;
  }
  return dueDate;
}

function _formatDateForDisplay(isoDate) {
  if (!isoDate) return "N/A";
  const parts = isoDate.split("-");
  if (parts.length !== 3) return isoDate;
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

function _normaliseDateCell(cell) {
  if (!cell) return "";
  if (cell instanceof Date) {
    return Utilities.formatDate(cell, "Asia/Kolkata", "yyyy-MM-dd");
  }
  const s = String(cell).trim();
  const m = s.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$/);
  if (m) {
    let [, d, mo, y] = m.map(Number);
    if (y < 100) y += 2000;
    return `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }
  return s;
}
