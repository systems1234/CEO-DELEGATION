(function () {
  async function request(url, options = {}) {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json", Accept: "application/json", ...(options.headers || {}) },
      ...options,
    });
    const isJson = (response.headers.get("content-type") || "").includes("application/json");
    const data = isJson ? await response.json() : null;
    if (!response.ok) {
      const message = (data && data.detail) || `Request failed with ${response.status}`;
      throw new Error(message);
    }
    return data;
  }

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  const LOCALE = "en-IN";
  const dateFormatter = new Intl.DateTimeFormat(LOCALE, { day: "2-digit", month: "short", year: "numeric" });

  function formatDate(value) {
    if (!value) return "Not set";
    const parsed = new Date(`${value}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) return value;
    return dateFormatter.format(parsed);
  }

  window.Api = { request, esc, formatDate };
})();
