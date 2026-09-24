const LOCALE = "en-IN";
const shortDateFormatter = new Intl.DateTimeFormat(LOCALE, { day: "2-digit", month: "short" });

const errorEl = document.getElementById("reports-error");
const totalEl = document.getElementById("kpi-total");
const completionRateEl = document.getElementById("kpi-completion-rate");
const overdueEl = document.getElementById("kpi-overdue");
const avgDaysEl = document.getElementById("kpi-avg-days");
const trendChartEl = document.getElementById("trend-chart");
const memberBodyEl = document.getElementById("member-kpi-body");
const memberEmptyEl = document.getElementById("member-kpi-empty");

function esc(text) {
  return String(text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function renderTrend(points) {
  const maxCompleted = Math.max(1, ...points.map((p) => p.completed));
  trendChartEl.innerHTML = points
    .map((point) => {
      const heightPct = Math.max(4, Math.round((point.completed / maxCompleted) * 100));
      const label = shortDateFormatter.format(new Date(`${point.date}T00:00:00`));
      return `
        <div class="trend-bar-wrap" title="${point.completed} completed on ${esc(label)}">
          <div class="trend-bar" style="height: ${heightPct}%"></div>
          <span class="trend-bar-label">${label}</span>
        </div>
      `;
    })
    .join("");
}

function renderMemberTable(members) {
  memberEmptyEl.hidden = members.length !== 0;
  memberBodyEl.innerHTML = members
    .map(
      (member) => `
      <tr>
        <td>${esc(member.name)}</td>
        <td class="num">${member.assigned}</td>
        <td class="num">${member.completed}</td>
        <td class="num">${member.pending}</td>
        <td class="num">${member.overdue}</td>
        <td class="num">${member.completion_rate.toFixed(0)}%</td>
      </tr>
    `
    )
    .join("");
}

async function fetchReports() {
  try {
    const response = await fetch("/api/reports", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Request failed with ${response.status}`);
    const report = await response.json();
    errorEl.hidden = true;

    totalEl.textContent = report.total_tasks;
    completionRateEl.textContent = `${report.completion_rate.toFixed(0)}%`;
    overdueEl.textContent = report.overdue_tasks;
    avgDaysEl.textContent = report.avg_completion_days === null
      ? "–"
      : report.avg_completion_days.toFixed(1);

    renderTrend(report.daily_trend || []);
    renderMemberTable(report.members || []);
  } catch (error) {
    console.error(error);
    errorEl.hidden = false;
  }
}

fetchReports();
window.setInterval(fetchReports, 60000);
