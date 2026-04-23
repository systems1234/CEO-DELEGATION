const body = document.body;
const dashboardUrl = body.dataset.dashboardUrl;
const taskUrl = body.dataset.taskUrl;

const state = {
  snapshot: null,
  filter: "all",
  refreshing: false,
};

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

const statusElements = {
  active: document.getElementById("stat-active"),
  dueToday: document.getElementById("stat-due-today"),
  overdue: document.getElementById("stat-overdue"),
  completed: document.getElementById("stat-completed"),
  team: document.getElementById("stat-team"),
  total: document.getElementById("stat-total"),
};

const filterButtons = Array.from(document.querySelectorAll(".filter-chip"));

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
}

function formatDate(value) {
  if (!value) return "Not set";
  const parts = value.split("-");
  if (parts.length !== 3) return value;
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

function taskMatchesFilter(task) {
  return state.filter === "all" || task.status === state.filter;
}

function renderTasks(tasks) {
  taskGrid.innerHTML = "";
  const visibleTasks = tasks.filter(taskMatchesFilter);

  taskEmpty.hidden = visibleTasks.length !== 0;
  if (visibleTasks.length === 0) {
    return;
  }

  visibleTasks.forEach((task) => {
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

    taskGrid.appendChild(fragment);
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
    : new Date(snapshot.generated_at).toLocaleString();
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
    button.addEventListener("click", () => {
      filterButtons.forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      state.filter = button.dataset.filter;
      if (state.snapshot) {
        renderTasks(state.snapshot.tasks);
      }
    });
  });
}

refreshButton.addEventListener("click", fetchDashboard);
assignForm.addEventListener("submit", submitTask);
bindFilters();
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

function fmtRecordTime(s) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

dmToggleCustom.addEventListener("click", () => {
  dmCustomVisible = !dmCustomVisible;
  dmCustomWrap.hidden = !dmCustomVisible;
  dmToggleCustom.textContent = dmCustomVisible ? "− Use team member" : "+ Custom number";
  if (dmCustomVisible) {
    dmRecipientSelect.value = "";
  } else {
    dmCustomNumber.value = "";
  }
});

dmTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    dmTabs.forEach((t) => { t.classList.remove("is-active"); t.setAttribute("aria-selected", "false"); });
    tab.classList.add("is-active");
    tab.setAttribute("aria-selected", "true");
    const mode = tab.dataset.mode;
    dmTextPanel.hidden = mode !== "text";
    dmFilePanel.hidden = mode !== "file";
    dmRecordPanel.hidden = mode !== "record";
    setDmStatus("");
  });
});

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
    };
    mediaRecorder.start();
    dmRecordBtn.innerHTML = '<span class="record-dot recording"></span>Stop';
    dmRecordHint.textContent = "Recording...";
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
  } catch (err) {
    setDmStatus(err.message || "Send failed.", "error");
  }
});
