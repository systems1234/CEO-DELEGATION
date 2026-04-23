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

const initialFilter = searchParams.get("filter");
if (["all", ...laneOrder].includes(initialFilter || "")) {
  state.filter = initialFilter;
}

function replaceFilterUrl(filter) {
  const nextParams = new URLSearchParams(window.location.search);
  if (filter && filter !== "all") {
    nextParams.set("filter", filter);
  } else {
    nextParams.delete("filter");
  }
  const nextQuery = nextParams.toString();
  window.history.replaceState({}, "", `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ""}`);
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
  memberRoster.innerHTML = "";
  assigneeSelect.innerHTML = '<option value="">Select a team member</option>';

  members.forEach((member) => {
    const option = document.createElement("option");
    option.value = member.name;
    option.textContent = member.name;
    assigneeSelect.appendChild(option);

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

    const data = response.headers.get("content-type")?.includes("application/json")
      ? await response.json()
      : { detail: await response.text() };
    if (!response.ok) {
      throw new Error(data.detail || "Task assignment failed");
    }

    assignForm.reset();
    setFormStatus(`Task assigned to ${data.task.assignee_name}.`, "success");
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
      replaceFilterUrl(state.filter);
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
