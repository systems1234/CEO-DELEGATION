const LOCALE = "en-IN";
const dateFormatter = new Intl.DateTimeFormat(LOCALE, { day: "2-digit", month: "short", year: "numeric" });

const overdueGroupsEl = document.getElementById("overdue-groups");
const overdueCountEl = document.getElementById("overdue-count");
const overdueEmptyEl = document.getElementById("overdue-empty");
const todayListEl = document.getElementById("today-list");
const todayCountEl = document.getElementById("today-count");
const todayEmptyEl = document.getElementById("today-empty");
const triageErrorEl = document.getElementById("triage-error");

function formatDate(value) {
  if (!value) return "No date";
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return dateFormatter.format(parsed);
}

function daysBetween(fromIso, toIso) {
  const from = new Date(`${fromIso}T00:00:00`);
  const to = new Date(`${toIso}T00:00:00`);
  return Math.round((to - from) / 86400000);
}

function esc(text) {
  return String(text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function taskRowHtml(task) {
  const priority = task.priority || "Medium";
  return `
    <div class="triage-row" data-row-id="${esc(task.row_id)}">
      <div class="triage-row-main">
        <div class="triage-row-top">
          <span class="priority-pill priority-${priority.toLowerCase()}">${priority}</span>
          <span class="triage-row-meta">${esc(task.status)} · Ref ${esc(task.row_id)}</span>
        </div>
        <p class="triage-row-task">${esc(task.task)}</p>
        <div class="triage-row-meta">Assigned to ${esc(task.assignee_name)} &middot; Due ${formatDate(task.new_date || task.due_date)}</div>
      </div>
      <div class="triage-row-actions">
        <button class="action-btn action-done" type="button" data-action="done">Mark Done</button>
        <button class="action-btn action-postpone" type="button" data-action="postpone">Postpone</button>
      </div>
    </div>
  `;
}

function renderTriage(view) {
  const overdueGroups = view.overdue_groups || [];
  const totalOverdue = overdueGroups.reduce((sum, g) => sum + g.tasks.length, 0);
  overdueCountEl.textContent = `${totalOverdue} task${totalOverdue === 1 ? "" : "s"}`;
  overdueEmptyEl.hidden = totalOverdue !== 0;

  overdueGroupsEl.innerHTML = overdueGroups
    .map((group) => {
      const overdueBy = daysBetween(group.date, view.today);
      const label = group.date === "Unknown" ? "Unknown date" : formatDate(group.date);
      return `
        <div class="date-group">
          <div class="date-group-head">
            <span>${label}</span>
            <span class="days-overdue">${overdueBy} day${overdueBy === 1 ? "" : "s"} overdue</span>
          </div>
          <div class="triage-row-list">
            ${group.tasks.map(taskRowHtml).join("")}
          </div>
        </div>
      `;
    })
    .join("");

  const todayTasks = view.due_today || [];
  todayCountEl.textContent = `${todayTasks.length} task${todayTasks.length === 1 ? "" : "s"}`;
  todayEmptyEl.hidden = todayTasks.length !== 0;
  todayListEl.innerHTML = todayTasks.map(taskRowHtml).join("");

  if (window.TaskActions) {
    window.TaskActions.bindActionButtons(overdueGroupsEl, fetchTriage);
    window.TaskActions.bindActionButtons(todayListEl, fetchTriage);
  }
}

async function fetchTriage() {
  try {
    const response = await fetch("/api/triage", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Request failed with ${response.status}`);
    const data = await response.json();
    triageErrorEl.hidden = true;
    renderTriage(data);
  } catch (error) {
    console.error(error);
    triageErrorEl.hidden = false;
  }
}

fetchTriage();
window.setInterval(fetchTriage, 30000);
