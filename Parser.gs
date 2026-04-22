// ─── NLP TASK PARSER ─────────────────────────────────────────────────────────

/**
 * Parses a natural-language task assignment message from the CEO into structured data.
 * Supports Hindi, Hinglish, and English.
 *
 * @param {string} rawMessage  The CEO's raw WhatsApp group message
 * @param {string[]} knownNames  Array of known member names from Config sheet (for fuzzy matching)
 * @returns {{ assignee: string, task: string, dueDate: string } | null}
 *   assignee  — name as it appears in the Config sheet (matched)
 *   task      — clean task description
 *   dueDate   — ISO date string YYYY-MM-DD, or null if not mentioned
 */
function parseTaskAssignment(rawMessage, knownNames) {
  if (!rawMessage || rawMessage.trim().length === 0) return null;

  const apiKey = getProp("OPENAI_API_KEY");

  const systemPrompt =
    `You are a task extraction assistant for an Indian business WhatsApp group. ` +
    `The CEO sends task assignments in Hindi, Hinglish, or English. ` +
    `Extract the assignment details and return ONLY valid JSON — no markdown, no explanation.\n\n` +
    `Known team member names: ${JSON.stringify(knownNames)}\n\n` +
    `Return this exact JSON shape:\n` +
    `{\n` +
    `  "assignee": "<name from the known list, best fuzzy match, or null if unclear>",\n` +
    `  "task": "<clean task description in English>",\n` +
    `  "due_date": "<YYYY-MM-DD or null if not mentioned>"\n` +
    `}\n\n` +
    `Rules:\n` +
    `- Today is ${_todayIso()}. Resolve relative dates ("kal", "tomorrow", "next Monday") against today.\n` +
    `- If a date is ambiguous (only day number given), assume the nearest future occurrence.\n` +
    `- If the message is NOT a task assignment, return {"assignee":null,"task":null,"due_date":null}.\n` +
    `- Assignee fuzzy matching: "Rahul ko", "Priya ne", "Amit bhai" → match against known names list.`;

  const payload = {
    model:       CFG.OPENAI_MODEL,
    temperature: 0,
    max_tokens:  256,
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user",   content: rawMessage },
    ],
  };

  const response = UrlFetchApp.fetch(CFG.OPENAI_URL, {
    method:             "post",
    contentType:        "application/json",
    headers:            { Authorization: `Bearer ${apiKey}` },
    payload:            JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  const code = response.getResponseCode();
  const raw  = response.getContentText();

  if (code !== 200) {
    Logger.log(`parseTaskAssignment OpenAI error ${code}: ${raw}`);
    return null;
  }

  let content;
  try {
    content = JSON.parse(raw)?.choices?.[0]?.message?.content ?? "";
    content = content.replace(/```json|```/gi, "").trim();
    const parsed = JSON.parse(content);

    if (!parsed.assignee || !parsed.task) {
      Logger.log(`parseTaskAssignment: non-assignment message or unparseable → ${content}`);
      return null;
    }

    return {
      assignee: parsed.assignee,
      task:     parsed.task,
      dueDate:  parsed.due_date ?? null,
    };
  } catch (e) {
    Logger.log(`parseTaskAssignment JSON parse failed: ${e.message} raw content: ${content}`);
    return null;
  }
}

/**
 * Parses an assignee's postpone reply to extract new date and reason.
 * Expected format (loosely): "NEW DATE: 25/04/2025 | REASON: client delay"
 * Falls back to OpenAI for messy free-text replies.
 *
 * @param {string} replyText
 * @returns {{ newDate: string|null, reason: string|null }}
 */
function parsePostponeReply(replyText) {
  if (!replyText) return { newDate: null, reason: null };

  // Fast path: structured format
  const structuredMatch = replyText.match(
    /new\s*date\s*[:\-]\s*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})[^\w]*reason\s*[:\-]\s*(.+)/i
  );
  if (structuredMatch) {
    return {
      newDate: _parseDateFlexible(structuredMatch[1].trim()),
      reason:  structuredMatch[2].trim(),
    };
  }

  // Slow path: OpenAI
  const apiKey = getProp("OPENAI_API_KEY");
  const systemPrompt =
    `Extract a postpone date and reason from this WhatsApp reply. ` +
    `Today is ${_todayIso()}. Reply ONLY with valid JSON:\n` +
    `{"new_date": "YYYY-MM-DD or null", "reason": "string or null"}`;

  const response = UrlFetchApp.fetch(CFG.OPENAI_URL, {
    method:             "post",
    contentType:        "application/json",
    headers:            { Authorization: `Bearer ${apiKey}` },
    payload:            JSON.stringify({
      model:       CFG.OPENAI_MODEL,
      temperature: 0,
      max_tokens:  128,
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user",   content: replyText },
      ],
    }),
    muteHttpExceptions: true,
  });

  if (response.getResponseCode() !== 200) return { newDate: null, reason: null };

  try {
    const content = JSON.parse(response.getContentText())
      ?.choices?.[0]?.message?.content
      ?.replace(/```json|```/gi, "")
      ?.trim() ?? "{}";
    const parsed = JSON.parse(content);
    return {
      newDate: parsed.new_date ?? null,
      reason:  parsed.reason ?? null,
    };
  } catch (_) {
    return { newDate: null, reason: null };
  }
}

// ─── DATE HELPERS ─────────────────────────────────────────────────────────────

/**
 * Returns today's date as YYYY-MM-DD in IST.
 * @returns {string}
 */
function _todayIso() {
  return Utilities.formatDate(new Date(), "Asia/Kolkata", "yyyy-MM-dd");
}

/**
 * Converts DD/MM/YYYY or DD-MM-YYYY or DD/MM/YY to YYYY-MM-DD.
 * @param {string} dateStr
 * @returns {string|null}
 */
function _parseDateFlexible(dateStr) {
  if (!dateStr) return null;
  const parts = dateStr.split(/[\/\-]/);
  if (parts.length !== 3) return null;

  let [d, m, y] = parts.map(Number);
  if (y < 100) y += 2000;
  if (isNaN(d) || isNaN(m) || isNaN(y)) return null;

  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}
