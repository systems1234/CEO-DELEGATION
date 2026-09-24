const LOCALE = "en-IN";
const dateFormatter = new Intl.DateTimeFormat(LOCALE, { day: "2-digit", month: "short", year: "numeric" });

const listEl = document.getElementById("my-tasks-list");
const emptyEl = document.getElementById("my-tasks-empty");
const errorEl = document.getElementById("my-tasks-error");
const unlinkedEl = document.getElementById("my-tasks-unlinked");
const emailEl = document.getElementById("my-tasks-email");

function formatDate(value) {
  if (!value) return "No date";
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return dateFormatter.format(parsed);
}

function esc(text) {
  return String(text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function taskRowHtml(task) {
  const priority = task.priority || "Medium";
  const isDone = task.status === "Done";
  return `
    <div class="triage-row" data-row-id="${esc(task.row_id)}">
      <div class="triage-row-main">
        <div class="triage-row-top">
          <span class="priority-pill priority-${priority.toLowerCase()}">${priority}</span>
          <span class="triage-row-meta">${esc(task.status)} &middot; Ref ${esc(task.row_id)}</span>
        </div>
        <p class="triage-row-task">${esc(task.task)}</p>
        <div class="triage-row-meta">Due ${formatDate(task.new_date || task.due_date)}</div>
      </div>
      ${
        isDone
          ? ""
          : `<div class="triage-row-actions">
               <button class="action-btn action-done" type="button" data-action="done">Mark Done</button>
               <button class="action-btn action-postpone" type="button" data-action="postpone">Postpone</button>
             </div>`
      }
    </div>
  `;
}

async function fetchMyTasks() {
  try {
    const response = await fetch("/api/my-tasks", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Request failed with ${response.status}`);
    const data = await response.json();
    errorEl.hidden = true;

    if (!data.linked) {
      emailEl.textContent = data.email || "";
      unlinkedEl.hidden = false;
      emptyEl.hidden = true;
      listEl.innerHTML = "";
      return;
    }

    const tasks = data.tasks || [];
    unlinkedEl.hidden = true;
    emptyEl.hidden = tasks.length !== 0;
    listEl.innerHTML = tasks.map(taskRowHtml).join("");

    if (window.TaskActions) {
      window.TaskActions.bindActionButtons(listEl, fetchMyTasks);
    }
  } catch (error) {
    console.error(error);
    errorEl.hidden = false;
  }
}

fetchMyTasks();
window.setInterval(fetchMyTasks, 30000);
