(function () {
  const { request, esc, formatDate } = window.Api;

  const state = { tasks: [] };
  const todayIso = new Date().toISOString().slice(0, 10);

  const listEl = document.getElementById("tasks-list");
  const emptyEl = document.getElementById("tasks-empty");
  const errorEl = document.getElementById("tasks-error");
  const loadingEl = document.getElementById("tasks-loading");
  const pager = window.Pagination.create(document.getElementById("tasks-pagination"), { pageSize: 8 });

  const completeModal = document.getElementById("complete-modal");
  const completeForm = document.getElementById("complete-form");
  const completeTaskId = document.getElementById("complete-task-id");
  const completeDoerTaskId = document.getElementById("complete-doer-task-id");
  const completeComments = document.getElementById("complete-comments");
  const completeStatus = document.getElementById("complete-status");

  const updateModal = document.getElementById("update-modal");
  const updateForm = document.getElementById("update-form");
  const updateTaskId = document.getElementById("update-task-id");
  const updateDoerTaskId = document.getElementById("update-doer-task-id");
  const updateStatusSelect = document.getElementById("update-status-select");
  const updateComments = document.getElementById("update-comments");
  const updateStatusMsg = document.getElementById("update-status-msg");

  function setFormStatus(el, message, tone) {
    el.textContent = message;
    el.className = "form-status" + (tone ? ` is-${tone}` : "");
  }

  function statusPillFor(view) {
    if (view.is_completed) return { label: "Completed", cls: "pill-status-completed" };
    if (view.latest_update && view.latest_update.task_update === "Abandon") {
      return { label: "Abandoned", cls: "pill-status-abandoned" };
    }
    return { label: "In Progress", cls: "pill-status-assigned" };
  }

  function isOverdue(view) {
    return !view.is_completed && view.effective_due_date && view.effective_due_date < todayIso;
  }

  function renderStats() {
    let active = 0;
    let completed = 0;
    let overdue = 0;
    state.tasks.forEach((view) => {
      if (view.is_completed) completed += 1;
      else active += 1;
      if (isOverdue(view)) overdue += 1;
    });
    document.getElementById("stat-active").textContent = active;
    document.getElementById("stat-completed").textContent = completed;
    document.getElementById("stat-overdue").textContent = overdue;
  }

  function renderCards(pageItems) {
    listEl.innerHTML = "";
    pageItems.forEach((view) => {
      const status = statusPillFor(view);
      const priority = view.assignment.priority || "Medium";
      const overdue = isOverdue(view);

      const card = document.createElement("article");
      card.className = "doer-card";
      card.innerHTML = `
        <div class="doer-card-top">
          <span class="pill pill-${priority.toLowerCase()}">${priority}</span>
          <span class="pill ${status.cls}">${esc(status.label)}</span>
        </div>
        <h3 class="doer-card-title">${esc(view.all_task.task_brief)}</h3>
        <div class="doer-card-meta">
          Assigned by ${esc(view.assignment.assigned_by)} &middot;
          Due ${esc(formatDate(view.effective_due_date))}${overdue ? " (overdue)" : ""}
        </div>
        ${view.assignment.comments ? `<div class="cell-muted">${esc(view.assignment.comments)}</div>` : ""}
        ${
          view.latest_update
            ? `<div class="update-log">Last update: <strong>${esc(view.latest_update.task_update)}</strong>${
                view.latest_update.comments ? ` — ${esc(view.latest_update.comments)}` : ""
              }</div>`
            : ""
        }
        ${
          view.is_completed
            ? `<div class="update-log">Completed ${esc(formatDate(view.completion.completion_date))}${
                view.completion.comments ? ` — ${esc(view.completion.comments)}` : ""
              }</div>`
            : ""
        }
        <div class="doer-card-actions"></div>
      `;

      if (!view.is_completed) {
        const actions = card.querySelector(".doer-card-actions");
        const completeBtn = document.createElement("button");
        completeBtn.type = "button";
        completeBtn.className = "btn btn-sm btn-primary";
        completeBtn.textContent = "Mark Complete";
        completeBtn.addEventListener("click", () => openCompleteModal(view));
        actions.appendChild(completeBtn);

        const updateBtn = document.createElement("button");
        updateBtn.type = "button";
        updateBtn.className = "btn btn-sm btn-ghost";
        updateBtn.textContent = "Post Update";
        updateBtn.addEventListener("click", () => openUpdateModal(view));
        actions.appendChild(updateBtn);
      }

      listEl.appendChild(card);
    });
  }

  function openCompleteModal(view) {
    completeTaskId.value = view.assignment.task_id;
    completeDoerTaskId.value = view.assignment.doer_task_id;
    completeComments.value = "";
    setFormStatus(completeStatus, "");
    completeModal.hidden = false;
  }

  function openUpdateModal(view) {
    updateTaskId.value = view.assignment.task_id;
    updateDoerTaskId.value = view.assignment.doer_task_id;
    updateStatusSelect.value = "In Progress";
    updateComments.value = "";
    setFormStatus(updateStatusMsg, "");
    updateModal.hidden = false;
  }

  async function loadTasks() {
    loadingEl.hidden = false;
    errorEl.hidden = true;
    listEl.hidden = true;
    emptyEl.hidden = true;
    try {
      const data = await request("/api/my-doer-tasks");
      state.tasks = data.tasks || [];
      loadingEl.hidden = true;
      if (state.tasks.length === 0) {
        emptyEl.hidden = false;
        renderStats();
        return;
      }
      listEl.hidden = false;
      renderStats();
      pager.setItems(state.tasks, renderCards);
    } catch (err) {
      loadingEl.hidden = true;
      errorEl.hidden = false;
    }
  }

  completeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setFormStatus(completeStatus, "Saving…");
    try {
      await request(`/api/doer-tasks/${encodeURIComponent(completeTaskId.value)}/complete`, {
        method: "POST",
        body: JSON.stringify({ doer_task_id: completeDoerTaskId.value, comments: completeComments.value }),
      });
      completeModal.hidden = true;
      await loadTasks();
    } catch (err) {
      setFormStatus(completeStatus, err.message || "Could not mark complete.", "error");
    }
  });

  updateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setFormStatus(updateStatusMsg, "Saving…");
    try {
      await request(`/api/doer-tasks/${encodeURIComponent(updateTaskId.value)}/update`, {
        method: "POST",
        body: JSON.stringify({
          doer_task_id: updateDoerTaskId.value,
          task_update: updateStatusSelect.value,
          comments: updateComments.value,
        }),
      });
      updateModal.hidden = true;
      await loadTasks();
    } catch (err) {
      setFormStatus(updateStatusMsg, err.message || "Could not post update.", "error");
    }
  });

  document.getElementById("complete-cancel").addEventListener("click", () => {
    completeModal.hidden = true;
  });
  document.getElementById("update-cancel").addEventListener("click", () => {
    updateModal.hidden = true;
  });
  document.getElementById("refresh-btn").addEventListener("click", loadTasks);

  loadTasks();
})();
