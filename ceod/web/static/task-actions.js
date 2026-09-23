(function () {
  let activePopover = null;

  function closePopover() {
    if (activePopover) {
      activePopover.remove();
      activePopover = null;
    }
    document.removeEventListener("click", handleOutsideClick, true);
  }

  function handleOutsideClick(event) {
    if (activePopover && !activePopover.contains(event.target)) {
      closePopover();
    }
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = response.headers.get("content-type")?.includes("application/json")
      ? await response.json()
      : { detail: await response.text() };
    if (!response.ok) {
      throw new Error(data.detail || "Request failed");
    }
    return data;
  }

  async function markDone(rowId, button, onChange) {
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "Marking...";
    try {
      await postJson(`/api/tasks/${encodeURIComponent(rowId)}/done`, null);
      if (onChange) await onChange();
    } catch (error) {
      alert(error.message || "Could not mark task done.");
      button.disabled = false;
      button.textContent = original;
    }
  }

  function openPostponePopover(anchor, rowId, onChange) {
    closePopover();
    const rect = anchor.getBoundingClientRect();
    const popover = document.createElement("div");
    popover.className = "postpone-popover";
    popover.innerHTML = `
      <label>
        <span>New date</span>
        <input type="date" class="pp-date" required />
      </label>
      <label>
        <span>Reason</span>
        <textarea class="pp-reason" rows="2" placeholder="Why is this being pushed out?" required></textarea>
      </label>
      <p class="pp-error" style="color:var(--danger); font-size:0.8rem; margin:0; display:none;"></p>
      <div class="postpone-popover-actions">
        <button type="button" class="action-btn pp-cancel">Cancel</button>
        <button type="button" class="action-btn action-done pp-submit">Reschedule</button>
      </div>
    `;
    document.body.appendChild(popover);

    const top = Math.min(rect.bottom + 8, window.innerHeight - popover.offsetHeight - 16);
    const left = Math.min(rect.left, window.innerWidth - popover.offsetWidth - 16);
    popover.style.top = `${Math.max(16, top) + window.scrollY}px`;
    popover.style.left = `${Math.max(16, left) + window.scrollX}px`;

    activePopover = popover;
    setTimeout(() => document.addEventListener("click", handleOutsideClick, true), 0);

    popover.querySelector(".pp-cancel").addEventListener("click", closePopover);
    popover.querySelector(".pp-submit").addEventListener("click", async () => {
      const newDate = popover.querySelector(".pp-date").value;
      const reason = popover.querySelector(".pp-reason").value.trim();
      const errorEl = popover.querySelector(".pp-error");
      if (!newDate || !reason) {
        errorEl.textContent = "Both a new date and a reason are required.";
        errorEl.style.display = "block";
        return;
      }
      const submitBtn = popover.querySelector(".pp-submit");
      submitBtn.disabled = true;
      try {
        await postJson(`/api/tasks/${encodeURIComponent(rowId)}/postpone`, { new_date: newDate, reason });
        closePopover();
        if (onChange) await onChange();
      } catch (error) {
        errorEl.textContent = error.message || "Could not reschedule task.";
        errorEl.style.display = "block";
        submitBtn.disabled = false;
      }
    });
  }

  function bindActionButtons(root, onChange) {
    root.querySelectorAll("[data-action='done']").forEach((button) => {
      button.addEventListener("click", () => {
        const rowId = button.closest("[data-row-id]")?.dataset.rowId;
        if (rowId) markDone(rowId, button, onChange);
      });
    });
    root.querySelectorAll("[data-action='postpone']").forEach((button) => {
      button.addEventListener("click", () => {
        const rowId = button.closest("[data-row-id]")?.dataset.rowId;
        if (rowId) openPostponePopover(button, rowId, onChange);
      });
    });
  }

  window.TaskActions = { bindActionButtons };
})();
