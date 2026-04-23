const body = document.body;
const dashboardUrl = body.dataset.dashboardUrl;
const taskUrl = body.dataset.taskUrl;

const state = {
  snapshot: null,
  filter: "all",
  refreshing: false,
};
const searchParams = new URLSearchParams(window.location.search);
const LOCALE = "en-IN";
const dateFormatter = new Intl.DateTimeFormat(LOCALE, {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});
const dateTimeFormatter = new Intl.DateTimeFormat(LOCALE, {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const memberRoster = document.getElementById("member-roster");
const assigneeSelect = document.getElementById("assignee-select");
const taskGrid = document.getElementById("task-grid");
const taskEmpty = document.getElementById("task-empty");
const taskError = document.getElementById("task-error");
const refreshButton = document.getElementById("refresh-button");
const refreshStatus = document.getElementById("refresh-status");
const assignForm = document.getElementById("assign-form");
const formStatus = document.getElementById("form-status");
const snapshotMeta = document.getElementById("snapshot-meta");
const cardTemplate = document.getElementById("task-card-template");
const railTabs = Array.from(document.querySelectorAll(".rail-tab"));
const railPanels = Array.from(document.querySelectorAll(".rail-panel"));

const statusElements = {
  active: document.getElementById("stat-active"),
  dueToday: document.getElementById("stat-due-today"),
  overdue: document.getElementById("stat-overdue"),
  completed: document.getElementById("stat-completed"),
  team: document.getElementById("stat-team"),
  total: document.getElementById("stat-total"),
};

const filterButtons = Array.from(document.querySelectorAll(".filter-chip"));
const laneOrder = ["Pending", "Postponed", "Overdue", "Done"];
const laneTitles = {
  Pending: "In progress",
  Postponed: "Moved out",
  Overdue: "Needs action",
  Done: "Closed",
};
const allowedRailPanels = new Set(["assign", "team", "whatsapp"]);
const allowedDmModes = new Set(["text", "file", "record"]);

const initialFilter = searchParams.get("filter");
if (["all", ...laneOrder].includes(initialFilter || "")) {
  state.filter = initialFilter;
}

function replaceUrlState(nextState) {
  const nextParams = new URLSearchParams(window.location.search);

  if (nextState.filter && nextState.filter !== "all") {
    nextParams.set("filter", nextState.filter);
  } else {
    nextParams.delete("filter");
  }

  if (nextState.panel && nextState.panel !== "assign") {
    nextParams.set("panel", nextState.panel);
  } else {
    nextParams.delete("panel");
  }

  if (nextState.mode && nextState.mode !== "text") {
    nextParams.set("mode", nextState.mode);
  } else {
    nextParams.delete("mode");
  }

  const nextQuery = nextParams.toString();
  const nextUrl = `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ""}`;
  window.history.replaceState({}, "", nextUrl);
}

function buildInitials(label) {
  return label
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase() || "WA";
}

function setRefreshStatus(message, isError = false) {
  refreshStatus.textContent = message;
  refreshStatus.parentElement.style.background = isError
    ? "rgba(143, 29, 34, 0.92)"
    : "rgba(32, 22, 16, 0.92)";
}

function setFormStatus(message, tone = "") {
  formStatus.textContent = message;
  formStatus.className = "form-status";
  if (tone) {
    formStatus.classList.add(`is-${tone}`);
  }
}

function updateStats(stats) {
  statusElements.active.textContent = stats.active_tasks;
  statusElements.dueToday.textContent = stats.due_today_tasks;
  statusElements.overdue.textContent = stats.overdue_tasks;
  statusElements.completed.textContent = stats.completed_tasks;
  statusElements.team.textContent = stats.team_members;
  statusElements.total.textContent = stats.total_tasks;
}

function renderMembers(members) {
  const currentValue = assigneeSelect.value;
  const dmCurrentValue = dmRecipientSelect.value;
  memberRoster.innerHTML = "";
  assigneeSelect.innerHTML = '<option value="">Select a team member</option>';
  dmRecipientSelect.innerHTML = '<option value="">Select team member</option>';

  members.forEach((member) => {
    const option = document.createElement("option");
    option.value = member.name;
    option.textContent = member.name;
    assigneeSelect.appendChild(option);

    const dmOption = document.createElement("option");
    dmOption.value = member.number;
    dmOption.textContent = `${member.name} (${member.number})`;
    dmOption.dataset.name = member.name;
    dmOption.dataset.number = member.number;
    dmRecipientSelect.appendChild(dmOption);

    const item = document.createElement("li");
    item.innerHTML = `
      <div>
        <div class="member-name">${member.name}</div>
        <div class="member-sheet">${member.sheet_name}</div>
      </div>
      <div class="member-sheet">${member.number}</div>
    `;
    memberRoster.appendChild(item);
  });

  if (members.some((member) => member.name === currentValue)) {
    assigneeSelect.value = currentValue;
  }
  if (members.some((member) => member.number === dmCurrentValue)) {
    dmRecipientSelect.value = dmCurrentValue;
  }

  updateDmRecipientDisplay();
}

function formatDate(value) {
  if (!value) return "Not set";
  const parsedDate = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsedDate.getTime())) return value;
  return dateFormatter.format(parsedDate);
}

function taskMatchesFilter(task) {
  return state.filter === "all" || task.status === state.filter;
}

function createTaskCard(task) {
  const fragment = cardTemplate.content.cloneNode(true);
  const card = fragment.querySelector(".task-card");
  const statusClass = `status-${task.status.toLowerCase()}`;
  card.classList.add(statusClass);

  fragment.querySelector(".status-pill").textContent = task.status;
  fragment.querySelector(".task-row-id").textContent = task.row_id;
  fragment.querySelector(".task-title").textContent = task.task;
  fragment.querySelector(".assignee").textContent = `Assigned to ${task.assignee_name}`;
  fragment.querySelector(".assign-date").textContent = formatDate(task.assign_date);
  fragment.querySelector(".due-date").textContent = formatDate(task.due_date);
  fragment.querySelector(".effective-date").textContent = formatDate(task.new_date || task.due_date);

  const reasonNode = fragment.querySelector(".task-reason");
  if (task.postpone_reason) {
    reasonNode.hidden = false;
    reasonNode.textContent = `Delay context: ${task.postpone_reason}`;
  }

  return fragment;
}

function renderTasks(tasks) {
  taskGrid.innerHTML = "";
  const visibleTasks = tasks.filter(taskMatchesFilter);

  taskEmpty.hidden = visibleTasks.length !== 0;
  if (visibleTasks.length === 0) {
    return;
  }

  const visibleStatuses = state.filter === "all"
    ? laneOrder
    : laneOrder.filter((status) => status === state.filter);

  visibleStatuses.forEach((status) => {
    const laneTasks = visibleTasks.filter((task) => task.status === status);
    const lane = document.createElement("section");
    lane.className = `task-lane task-lane-${status.toLowerCase()}`;
    lane.innerHTML = `
      <header class="task-lane-head">
        <div>
          <p class="task-lane-kicker">${status}</p>
          <h3 class="task-lane-title">${laneTitles[status]}</h3>
        </div>
        <span class="task-lane-count">${laneTasks.length}</span>
      </header>
      <div class="task-lane-list"></div>
    `;

    const laneList = lane.querySelector(".task-lane-list");
    if (laneTasks.length === 0) {
      const emptyState = document.createElement("div");
      emptyState.className = "task-lane-empty";
      emptyState.textContent = "No tasks in this lane.";
      laneList.appendChild(emptyState);
    } else {
      laneTasks.forEach((task) => {
        laneList.appendChild(createTaskCard(task));
      });
    }

    taskGrid.appendChild(lane);
  });
}

function renderSnapshot(snapshot) {
  state.snapshot = snapshot;
  taskError.hidden = true;
  updateStats(snapshot.stats);
  renderMembers(snapshot.members);
  renderTasks(snapshot.tasks);
  const generatedAt = Number.isNaN(Date.parse(snapshot.generated_at))
    ? snapshot.generated_at
    : dateTimeFormatter.format(new Date(snapshot.generated_at));
  snapshotMeta.textContent = `Board synced at ${generatedAt} | ${snapshot.tasks.length} tasks in timeline order`;
}

async function fetchDashboard() {
  if (state.refreshing) {
    return;
  }

  state.refreshing = true;
  taskError.hidden = true;
  setRefreshStatus("Syncing board...");

  try {
    const response = await fetch(dashboardUrl, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error(`Dashboard request failed with ${response.status}`);
    }
    const snapshot = await response.json();
    renderSnapshot(snapshot);
    setRefreshStatus("Live sync active");
  } catch (error) {
    console.error(error);
    taskError.hidden = false;
    setRefreshStatus("Live sync paused", true);
  } finally {
    state.refreshing = false;
  }
}

async function submitTask(event) {
  event.preventDefault();
  setFormStatus("Assigning task...");

  const payload = {
    assignee: assigneeSelect.value,
    task: document.getElementById("task-input").value.trim(),
    due_date: document.getElementById("due-date-input").value || null,
  };

  try {
    const response = await fetch(taskUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });

    const body = response.headers.get("content-type")?.includes("application/json")
      ? await response.json()
      : { detail: await response.text() };
    if (!response.ok) {
      throw new Error(body.detail || "Task assignment failed");
    }

    assignForm.reset();
    setFormStatus(`Task assigned to ${body.task.assignee_name}.`, "success");
    await fetchDashboard();
  } catch (error) {
    console.error(error);
    setFormStatus(error.message || "Task assignment failed.", "error");
  }
}

function bindFilters() {
  filterButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.filter === state.filter);
    button.addEventListener("click", () => {
      filterButtons.forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      state.filter = button.dataset.filter;
      replaceUrlState({
        filter: state.filter,
        panel: document.querySelector(".rail-tab.is-active")?.dataset.railPanel || "assign",
        mode: getActiveDmMode(),
      });
      if (state.snapshot) {
        renderTasks(state.snapshot.tasks);
      }
    });
  });
}

function activateRailPanel(panelName) {
  const targetPanel = allowedRailPanels.has(panelName) ? panelName : "assign";

  railTabs.forEach((tab) => {
    const isActive = tab.dataset.railPanel === targetPanel;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
  });

  railPanels.forEach((panel) => {
    const isActive = panel.id === `rail-panel-${targetPanel}`;
    panel.classList.toggle("is-active", isActive);
    panel.hidden = !isActive;
  });

  replaceUrlState({
    filter: state.filter,
    panel: targetPanel,
    mode: getActiveDmMode(),
  });
}

function bindRailTabs() {
  railTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      activateRailPanel(tab.dataset.railPanel || "assign");
    });
  });
}

refreshButton.addEventListener("click", fetchDashboard);
assignForm.addEventListener("submit", submitTask);
bindFilters();
bindRailTabs();
fetchDashboard();
window.setInterval(fetchDashboard, 30000);

// --- Direct Message ---
const dmRecipientSelect = document.getElementById("dm-recipient-select");
const dmCustomWrap = document.getElementById("dm-custom-wrap");
const dmCustomNumber = document.getElementById("dm-custom-number");
const dmToggleCustom = document.getElementById("dm-toggle-custom");
const dmTabs = Array.from(document.querySelectorAll(".dm-tab"));
const dmTextPanel = document.getElementById("dm-text-panel");
const dmFilePanel = document.getElementById("dm-file-panel");
const dmRecordPanel = document.getElementById("dm-record-panel");
const dmTextInput = document.getElementById("dm-text-input");
const dmFileInput = document.getElementById("dm-file-input");
const dmCaptionInput = document.getElementById("dm-caption-input");
const dmSendText = document.getElementById("dm-send-text");
const dmSendFile = document.getElementById("dm-send-file");
const dmRecordBtn = document.getElementById("dm-record-btn");
const dmRecordTimer = document.getElementById("dm-record-timer");
const dmRecordHint = document.getElementById("dm-record-hint");
const dmSendVoice = document.getElementById("dm-send-voice");
const dmStatus = document.getElementById("dm-status");
const dmContactName = document.getElementById("dm-contact-name");
const dmContactMeta = document.getElementById("dm-contact-meta");
const dmAvatar = document.getElementById("dm-avatar");
const dmPreviewBubble = document.getElementById("dm-preview-bubble");

let dmCustomVisible = false;
let mediaRecorder = null;
let recordedChunks = [];
let recordedBlob = null;
let recordTimerInterval = null;
let recordSeconds = 0;

function setDmStatus(message, tone = "") {
  dmStatus.textContent = message;
  dmStatus.className = "form-status";
  if (tone) dmStatus.classList.add(`is-${tone}`);
}

function getDmRecipient() {
  if (dmCustomVisible) return dmCustomNumber.value.trim();
  return dmRecipientSelect.value;
}

function getActiveDmMode() {
  return dmTabs.find((tab) => tab.classList.contains("is-active"))?.dataset.mode || "text";
}

function activateDmMode(mode) {
  const nextMode = allowedDmModes.has(mode) ? mode : "text";
  dmTabs.forEach((tab) => {
    const isActive = tab.dataset.mode === nextMode;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
  });

  dmTextPanel.hidden = nextMode !== "text";
  dmFilePanel.hidden = nextMode !== "file";
  dmRecordPanel.hidden = nextMode !== "record";

  replaceUrlState({
    filter: state.filter,
    panel: document.querySelector(".rail-tab.is-active")?.dataset.railPanel || "assign",
    mode: nextMode,
  });
  updateDmPreview();
}

function fmtRecordTime(s) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function updateDmRecipientDisplay() {
  if (dmCustomVisible) {
    const customNumber = dmCustomNumber.value.trim();
    dmContactName.textContent = customNumber ? "Custom number" : "Select recipient";
    dmContactMeta.textContent = customNumber ? `+${customNumber}` : "Enter a custom WhatsApp number";
    dmAvatar.textContent = customNumber ? "CU" : "WA";
    return;
  }

  const selectedOption = dmRecipientSelect.selectedOptions[0];
  const selectedName = selectedOption?.dataset.name || "";
  const selectedNumber = selectedOption?.dataset.number || dmRecipientSelect.value;

  if (selectedName && selectedNumber) {
    dmContactName.textContent = selectedName;
    dmContactMeta.textContent = `+${selectedNumber}`;
    dmAvatar.textContent = buildInitials(selectedName);
    return;
  }

  dmContactName.textContent = "Select recipient";
  dmContactMeta.textContent = "Choose a team member or enter a custom number";
  dmAvatar.textContent = "WA";
}

function setDmPreview(text, placeholder = false) {
  dmPreviewBubble.textContent = text;
  dmPreviewBubble.classList.toggle("is-placeholder", placeholder);
}

function updateDmPreview() {
  const mode = getActiveDmMode();

  if (mode === "text") {
    const message = dmTextInput.value.trim();
    setDmPreview(message || "Type a message to preview it here.", !message);
    return;
  }

  if (mode === "file") {
    const file = dmFileInput.files[0];
    const caption = dmCaptionInput.value.trim();
    if (!file) {
      setDmPreview("Attach an image, document, audio, or video file.", true);
      return;
    }
    setDmPreview(caption ? `${file.name}\n${caption}` : file.name);
    return;
  }

  if (!recordedBlob) {
    setDmPreview("Record a voice note to preview it here.", true);
    return;
  }

  setDmPreview(`Voice note ready · ${fmtRecordTime(recordSeconds)}`);
}

dmToggleCustom.addEventListener("click", () => {
  dmCustomVisible = !dmCustomVisible;
  dmCustomWrap.hidden = !dmCustomVisible;
  dmToggleCustom.textContent = dmCustomVisible ? "- Use team member" : "+ Custom number";
  if (dmCustomVisible) {
    dmRecipientSelect.value = "";
  } else {
    dmCustomNumber.value = "";
  }
  updateDmRecipientDisplay();
});

dmTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    activateDmMode(tab.dataset.mode || "text");
    setDmStatus("");
  });
});

dmRecipientSelect.addEventListener("change", updateDmRecipientDisplay);
dmCustomNumber.addEventListener("input", updateDmRecipientDisplay);
dmTextInput.addEventListener("input", updateDmPreview);
dmFileInput.addEventListener("change", updateDmPreview);
dmCaptionInput.addEventListener("input", updateDmPreview);

dmSendText.addEventListener("click", async () => {
  const to = getDmRecipient();
  const message = dmTextInput.value.trim();
  if (!to) { setDmStatus("Select a recipient.", "error"); return; }
  if (!message) { setDmStatus("Enter a message.", "error"); return; }
  setDmStatus("Sending...");
  try {
    const resp = await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ to, message }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || "Send failed");
    }
    dmTextInput.value = "";
    setDmStatus("Message sent.", "success");
    updateDmPreview();
  } catch (err) {
    setDmStatus(err.message || "Send failed.", "error");
  }
});

dmSendFile.addEventListener("click", async () => {
  const to = getDmRecipient();
  const file = dmFileInput.files[0];
  if (!to) { setDmStatus("Select a recipient.", "error"); return; }
  if (!file) { setDmStatus("Choose a file first.", "error"); return; }
  setDmStatus("Uploading...");
  const form = new FormData();
  form.append("to", to);
  form.append("caption", dmCaptionInput.value.trim());
  form.append("file", file, file.name);
  try {
    const resp = await fetch("/api/send-media", { method: "POST", body: form });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || "Upload failed");
    }
    dmFileInput.value = "";
    dmCaptionInput.value = "";
    setDmStatus("File sent.", "success");
    updateDmPreview();
  } catch (err) {
    setDmStatus(err.message || "Upload failed.", "error");
  }
});

dmRecordBtn.addEventListener("click", async () => {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    recordedBlob = null;
    dmSendVoice.hidden = true;
    recordSeconds = 0;
    dmRecordTimer.textContent = fmtRecordTime(0);
    dmRecordTimer.hidden = false;
    recordTimerInterval = setInterval(() => {
      recordSeconds++;
      dmRecordTimer.textContent = fmtRecordTime(recordSeconds);
    }, 1000);

    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) recordedChunks.push(e.data); };
    mediaRecorder.onstop = () => {
      clearInterval(recordTimerInterval);
      stream.getTracks().forEach((t) => t.stop());
      const mimeType = mediaRecorder.mimeType || "audio/webm";
      recordedBlob = new Blob(recordedChunks, { type: mimeType });
      dmRecordBtn.innerHTML = '<span class="record-dot"></span>Record Again';
      dmRecordHint.textContent = `Recorded ${fmtRecordTime(recordSeconds)}. Ready to send.`;
      dmSendVoice.hidden = false;
      dmRecordTimer.hidden = true;
      updateDmPreview();
    };
    mediaRecorder.start();
    dmRecordBtn.innerHTML = '<span class="record-dot recording"></span>Stop';
    dmRecordHint.textContent = "Recording...";
    updateDmPreview();
  } catch {
    setDmStatus("Microphone access denied.", "error");
  }
});

dmSendVoice.addEventListener("click", async () => {
  const to = getDmRecipient();
  if (!to) { setDmStatus("Select a recipient.", "error"); return; }
  if (!recordedBlob) { setDmStatus("Record a voice note first.", "error"); return; }
  setDmStatus("Sending voice note...");
  const ext = recordedBlob.type.includes("ogg") ? "ogg" : "webm";
  const form = new FormData();
  form.append("to", to);
  form.append("caption", "");
  form.append("file", recordedBlob, `voice.${ext}`);
  try {
    const resp = await fetch("/api/send-media", { method: "POST", body: form });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || "Send failed");
    }
    recordedBlob = null;
    recordedChunks = [];
    dmSendVoice.hidden = true;
    dmRecordHint.textContent = "Press record, speak, then stop.";
    dmRecordTimer.hidden = true;
    dmRecordBtn.innerHTML = '<span class="record-dot"></span>Record';
    setDmStatus("Voice note sent.", "success");
    updateDmPreview();
  } catch (err) {
    setDmStatus(err.message || "Send failed.", "error");
  }
});

updateDmRecipientDisplay();
activateRailPanel(searchParams.get("panel") || "assign");
activateDmMode(searchParams.get("mode") || "text");
