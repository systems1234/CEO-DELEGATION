(function () {
  const STORAGE_KEY = "ceod_sidebar_collapsed";
  const sidebar = document.getElementById("app-sidebar");
  const toggle = document.getElementById("sidebar-toggle");
  if (!sidebar || !toggle) return;

  function applyState(collapsed) {
    sidebar.classList.toggle("is-collapsed", collapsed);
  }

  let stored = false;
  try {
    stored = window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch (err) {
    stored = false;
  }
  applyState(stored);

  toggle.addEventListener("click", () => {
    const collapsed = !sidebar.classList.contains("is-collapsed");
    applyState(collapsed);
    try {
      window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
    } catch (err) {
      /* ignore storage errors (private mode, etc.) */
    }
  });
})();
