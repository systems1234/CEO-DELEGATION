(function () {
  const { request, esc, formatDate } = window.Api;

  const state = { items: [], team: [] };

  const tableEl = document.getElementById("queue-table");
  const bodyEl = document.getElementById("queue-body");
  const emptyEl = document.getElementById("queue-empty");
  const errorEl = document.getElementById("queue-error");
  const loadingEl = document.getElementById("queue-loading");
  const pager = window.Pagination.create(document.getElementById("queue-pagination"), { pageSize: 10 });

  const assignModal = document.getElementById("assign-modal");
  const assignForm = document.getElementById("assign-form");
  const assignTaskIdInput = document.getElementById("assign-task-id");
  const assignDoerSelect = document.getElementById("assign-doer-select");
  const assignComments = document.getElementById("assign-comments");
  const assignPriority = document.getElementById("assign-priority");
  const assignDueDate = document.getElementById("assign-due-date");
  const assignStatus = document.getElementById("assign-status");
  const assignModalTitle = document.getElementById("assign-modal-title");

  const reviseModal = document.getElementById("revise-modal");
  const reviseForm = document.getElementById("revise-form");
  const reviseTaskIdInput = document.getElementById("revise-task-id");
  const reviseDateInput = document.getElementById("revise-date");
  const reviseStatus = document.getElementById("revise-status");

  function setFormStatus(el, message, tone) {
    el.textContent = message;
    el.className = "form-status" + (tone ? ` is-${tone}` : "");
  }

  function rowStatus(item) {
    if (item.completion) return { label: "Completed", cls: "pill-status-completed" };
    if (!item.current_assignment) return { label: "Unassigned", cls: "pill-status-unassigned" };
    if (item.latest_update && item.latest_update.task_update === "Abandon") {
      return { label: "Abandoned", cls: "pill-status-abandoned" };
    }
    return { label: `Assigned · ${item.current_assignment.assigned_to_doer}`, cls: "pill-status-assigned" };
  }

  function effectiveDueDate(item) {
    if (item.revisions && item.revisions.length) {
      const latest = item.revisions[item.revisions.length - 1];
      return { text: `${formatDate(latest.revised_date)} (revised)`, muted: false };
    }
    if (item.current_assignment && item.current_assignment.department_due_date) {
      return { text: formatDate(item.current_assignment.department_due_date), muted: false };
    }
    if (item.task.ceo_task_due_date) {
      return { text: `${formatDate(item.task.ceo_task_due_date)} (CEO)`, muted: true };
    }
    return { text: "Not set", muted: true };
  }

  function renderStats() {
    let unassigned = 0;
    let inProgress = 0;
    let completed = 0;
    let abandoned = 0;
    state.items.forEach((item) => {
      if (item.completion) completed += 1;
      else if (!item.current_assignment) unassigned += 1;
      else if (item.latest_update && item.latest_update.task_update === "Abandon") abandoned += 1;
      else inProgress += 1;
    });
    document.getElementById("stat-unassigned").textContent = unassigned;
    document.getElementById("stat-in-progress").textContent = inProgress;
    document.getElementById("stat-completed").textContent = completed;
    document.getElementById("stat-abandoned").textContent = abandoned;
  }

  function renderRows(pageItems) {
    bodyEl.innerHTML = "";
    pageItems.forEach((item) => {
      const status = rowStatus(item);
      const due = effectiveDueDate(item);
      const priority = item.task.priority || "Medium";
      const isCompleted = Boolean(item.completion);

      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>
          <div class="task-brief-cell">${esc(item.task.task_brief)}</div>
          <div class="cell-muted">Ref ${esc(item.task.task_id)}</div>
        </td>
        <td><span class="pill pill-${priority.toLowerCase()}">${priority}</span></td>
        <td>${esc(formatDate(item.task.ceo_task_due_date))}</td>
        <td><span class="pill ${status.cls}">${esc(status.label)}</span></td>
        <td${due.muted ? ' class="cell-muted"' : ""}>${esc(due.text)}</td>
        <td></td>
      `;
      const actionsCell = tr.lastElementChild;
      if (!isCompleted) {
        const assignBtn = document.createElement("button");
        assignBtn.type = "button";
        assignBtn.className = "btn btn-sm btn-primary";
        assignBtn.textContent = item.current_assignment ? "Reassign" : "Assign";
        assignBtn.addEventListener("click", () => openAssignModal(item));
        actionsCell.appendChild(assignBtn);

        if (item.current_assignment) {
          const reviseBtn = document.createElement("button");
          reviseBtn.type = "button";
          reviseBtn.className = "btn btn-sm btn-ghost";
          reviseBtn.style.marginLeft = "6px";
          reviseBtn.textContent = "Extra time";
          reviseBtn.addEventListener("click", () => openReviseModal(item));
          actionsCell.appendChild(reviseBtn);
        }
      }
      bodyEl.appendChild(tr);
    });
  }

  function openAssignModal(item) {
    assignTaskIdInput.value = item.task.task_id;
    assignModalTitle.textContent = item.current_assignment ? "Reassign task" : "Assign task";
    assignComments.value = "";
    assignPriority.value = item.task.priority || "Medium";
    assignDueDate.value = "";
    setFormStatus(assignStatus, "");
    assignDoerSelect.innerHTML = state.team
      .map((member) => `<option value="${esc(member.emp_id)}">${esc(member.emp_name)}</option>`)
      .join("");
    if (!state.team.length) {
      assignDoerSelect.innerHTML = '<option value="">No team members found — add them in Admin</option>';
    }
    assignModal.hidden = false;
  }

  function closeAssignModal() {
    assignModal.hidden = true;
  }

  function openReviseModal(item) {
    reviseTaskIdInput.value = item.task.task_id;
    reviseDateInput.value = "";
    setFormStatus(reviseStatus, "");
    reviseModal.hidden = false;
  }

  function closeReviseModal() {
    reviseModal.hidden = true;
  }

  async function loadTeam() {
    try {
      const data = await request("/api/team");
      state.team = data.team || [];
    } catch (err) {
      state.team = [];
    }
  }

  async function loadQueue() {
    loadingEl.hidden = false;
    errorEl.hidden = true;
    tableEl.hidden = true;
    emptyEl.hidden = true;
    try {
      const data = await request("/api/department-queue");
      state.items = data.items || [];
      loadingEl.hidden = true;
      if (state.items.length === 0) {
        emptyEl.hidden = false;
        renderStats();
        return;
      }
      tableEl.hidden = false;
      renderStats();
      pager.setItems(state.items, renderRows);
    } catch (err) {
      loadingEl.hidden = true;
      errorEl.hidden = false;
    }
  }

  assignForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setFormStatus(assignStatus, "Assigning…");
    try {
      await request(`/api/department-tasks/${encodeURIComponent(assignTaskIdInput.value)}/assign`, {
        method: "POST",
        body: JSON.stringify({
          assigned_to_doer: assignDoerSelect.value,
          comments: assignComments.value,
          due_date: assignDueDate.value || null,
          priority: assignPriority.value,
        }),
      });
      closeAssignModal();
      await loadQueue();
    } catch (err) {
      setFormStatus(assignStatus, err.message || "Could not assign task.", "error");
    }
  });

  reviseForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setFormStatus(reviseStatus, "Saving…");
    try {
      await request(`/api/department-tasks/${encodeURIComponent(reviseTaskIdInput.value)}/revise`, {
        method: "POST",
        body: JSON.stringify({ revised_date: reviseDateInput.value }),
      });
      closeReviseModal();
      await loadQueue();
    } catch (err) {
      setFormStatus(reviseStatus, err.message || "Could not save new date.", "error");
    }
  });

  document.getElementById("assign-cancel").addEventListener("click", closeAssignModal);
  document.getElementById("revise-cancel").addEventListener("click", closeReviseModal);
  document.getElementById("refresh-btn").addEventListener("click", loadQueue);

  loadTeam();
  loadQueue();
})();
