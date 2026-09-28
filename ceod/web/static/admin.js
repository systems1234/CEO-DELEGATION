(function () {
  const { request, esc } = window.Api;

  const usersLoading = document.getElementById("users-loading");
  const usersTable = document.getElementById("users-table");
  const usersBody = document.getElementById("users-body");
  const userForm = document.getElementById("user-form");
  const userFormStatus = document.getElementById("user-form-status");

  const reportingLoading = document.getElementById("reporting-loading");
  const reportingTable = document.getElementById("reporting-table");
  const reportingBody = document.getElementById("reporting-body");
  const reportingForm = document.getElementById("reporting-form");
  const reportingFormStatus = document.getElementById("reporting-form-status");

  function setFormStatus(el, message, tone) {
    el.textContent = message;
    el.className = "form-status" + (tone ? ` is-${tone}` : "");
  }

  async function loadUsers() {
    usersLoading.hidden = false;
    usersTable.hidden = true;
    try {
      const data = await request("/api/admin/users");
      const users = data.users || [];
      usersBody.innerHTML = users
        .map(
          (user) => `
        <tr>
          <td>${esc(user.user_id)}</td>
          <td>${esc(user.name)}</td>
          <td>${esc(user.role)}</td>
          <td>${user.active ? "Yes" : "No"}</td>
          <td>
            <button class="btn btn-sm btn-ghost" type="button" data-edit="${esc(user.user_id)}">Edit</button>
            ${
              user.active
                ? `<button class="btn btn-sm btn-danger-ghost" type="button" data-deactivate="${esc(user.user_id)}">Deactivate</button>`
                : ""
            }
          </td>
        </tr>
      `
        )
        .join("");

      usersBody.querySelectorAll("[data-edit]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const user = users.find((u) => u.user_id === btn.dataset.edit);
          if (!user) return;
          document.getElementById("user-email").value = user.user_id;
          document.getElementById("user-name").value = user.name;
          document.getElementById("user-role").value = user.role;
          document.getElementById("user-active").value = String(user.active);
        });
      });
      usersBody.querySelectorAll("[data-deactivate]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try {
            await request(`/api/admin/users/${encodeURIComponent(btn.dataset.deactivate)}/deactivate`, { method: "POST" });
            await loadUsers();
          } catch (err) {
            setFormStatus(userFormStatus, err.message || "Could not deactivate user.", "error");
          }
        });
      });

      usersLoading.hidden = true;
      usersTable.hidden = false;
    } catch (err) {
      usersLoading.textContent = "Could not load users.";
    }
  }

  async function loadReporting() {
    reportingLoading.hidden = false;
    reportingTable.hidden = true;
    try {
      const data = await request("/api/admin/reporting");
      const relations = data.relations || [];
      reportingBody.innerHTML = relations
        .map(
          (r) => `
        <tr>
          <td>${esc(r.tl_name || "—")}</td>
          <td>${esc(r.tl_email || "—")}</td>
          <td>${esc(r.manager_name || "—")}</td>
          <td>${esc(r.manager_email || "—")}</td>
          <td>${esc(r.emp_name)}</td>
          <td>${esc(r.emp_id)}</td>
        </tr>
      `
        )
        .join("");
      reportingLoading.hidden = true;
      reportingTable.hidden = false;
    } catch (err) {
      reportingLoading.textContent = "Could not load team structure.";
    }
  }

  userForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setFormStatus(userFormStatus, "Saving…");
    try {
      await request("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({
          email: document.getElementById("user-email").value,
          name: document.getElementById("user-name").value,
          role: document.getElementById("user-role").value,
          active: document.getElementById("user-active").value === "true",
        }),
      });
      userForm.reset();
      setFormStatus(userFormStatus, "Saved.", "success");
      await loadUsers();
    } catch (err) {
      setFormStatus(userFormStatus, err.message || "Could not save user.", "error");
    }
  });

  reportingForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setFormStatus(reportingFormStatus, "Saving…");
    try {
      await request("/api/admin/reporting", {
        method: "POST",
        body: JSON.stringify({
          tl_name: document.getElementById("reporting-tl").value || null,
          tl_email: document.getElementById("reporting-tl-email").value || null,
          manager_name: document.getElementById("reporting-manager").value || null,
          manager_email: document.getElementById("reporting-manager-email").value || null,
          emp_name: document.getElementById("reporting-emp-name").value,
          emp_id: document.getElementById("reporting-emp-id").value,
        }),
      });
      reportingForm.reset();
      setFormStatus(reportingFormStatus, "Added.", "success");
      await loadReporting();
    } catch (err) {
      setFormStatus(reportingFormStatus, err.message || "Could not add relation.", "error");
    }
  });

  loadUsers();
  loadReporting();
})();
