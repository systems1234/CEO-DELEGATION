(function () {
  function create(containerEl, { pageSize = 10 } = {}) {
    let items = [];
    let page = 1;
    let onRenderPage = () => {};

    function totalPages() {
      return Math.max(1, Math.ceil(items.length / pageSize));
    }

    function renderControls() {
      containerEl.innerHTML = "";
      if (items.length === 0) return;

      const total = totalPages();
      const info = document.createElement("span");
      info.className = "pagination-info";
      const start = (page - 1) * pageSize + 1;
      const end = Math.min(items.length, page * pageSize);
      info.textContent = `${start}-${end} of ${items.length}`;
      containerEl.appendChild(info);

      if (total <= 1) return;

      const controls = document.createElement("div");
      controls.className = "pagination-controls";

      const prev = document.createElement("button");
      prev.type = "button";
      prev.className = "pagination-btn";
      prev.textContent = "Prev";
      prev.disabled = page === 1;
      prev.addEventListener("click", () => goTo(page - 1));
      controls.appendChild(prev);

      const maxButtons = 5;
      let startPage = Math.max(1, page - Math.floor(maxButtons / 2));
      let endPage = Math.min(total, startPage + maxButtons - 1);
      startPage = Math.max(1, endPage - maxButtons + 1);
      for (let p = startPage; p <= endPage; p += 1) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "pagination-btn" + (p === page ? " is-active" : "");
        btn.textContent = String(p);
        btn.addEventListener("click", () => goTo(p));
        controls.appendChild(btn);
      }

      const next = document.createElement("button");
      next.type = "button";
      next.className = "pagination-btn";
      next.textContent = "Next";
      next.disabled = page === total;
      next.addEventListener("click", () => goTo(page + 1));
      controls.appendChild(next);

      containerEl.appendChild(controls);
    }

    function goTo(target) {
      page = Math.min(Math.max(1, target), totalPages());
      onRenderPage(items.slice((page - 1) * pageSize, page * pageSize));
      renderControls();
    }

    return {
      setItems(newItems, renderPageFn) {
        items = newItems || [];
        onRenderPage = renderPageFn;
        page = 1;
        goTo(1);
      },
      refresh() {
        goTo(page);
      },
    };
  }

  window.Pagination = { create };
})();
