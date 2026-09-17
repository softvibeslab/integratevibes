(() => {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  const token = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const headers = { "Content-Type": "application/json", "X-Miniapp-Token": token };
  const labels = {
    active: "Conectado",
    pending: "Autorización pendiente",
    expired: "Sesión caducada",
    disconnected: "Sin conectar",
    available: "Disponible sin conexión",
  };
  const PAGE_SIZE = 24;
  const list = document.getElementById("list");
  const empty = document.getElementById("empty");
  const msg = document.getElementById("message");
  const refresh = document.getElementById("refresh");
  const search = document.getElementById("integration-search");
  const filters = document.getElementById("filters");
  const resultsCount = document.getElementById("results-count");
  const activeFilter = document.getElementById("active-filter");
  const loadMore = document.getElementById("load-more");

  let catalog = [];
  let viewMode = "recommended";
  let visibleLimit = PAGE_SIZE;
  let lastLoadedAt = 0;
  let loading = false;
  let toastTimer;

  function toast(text, type = "ok") {
    window.clearTimeout(toastTimer);
    msg.textContent = text;
    msg.className = `message show ${type}`;
    toastTimer = window.setTimeout(() => { msg.className = "message"; }, 4500);
  }

  function normalized(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  }

  function matches(toolkit, query) {
    if (!query) {
      if (viewMode === "recommended") return toolkit.recommended;
      if (viewMode === "connected") return toolkit.status === "active";
      return true;
    }
    const haystack = [
      toolkit.label,
      toolkit.category,
      toolkit.description,
      ...(toolkit.capabilities || []),
      ...(toolkit.search_terms || []),
    ].join(" ");
    return normalized(haystack).includes(normalized(query));
  }

  function createToolkitCard(toolkit) {
    const article = document.createElement("article");
    article.className = "item";
    article.dataset.toolkit = toolkit.slug;
    article.dataset.state = toolkit.status;

    const mark = document.createElement("div");
    mark.className = "mark";
    mark.textContent = toolkit.mark;
    mark.setAttribute("aria-hidden", "true");

    const main = document.createElement("div");
    main.className = "item-main";
    const head = document.createElement("div");
    head.className = "item-head";
    const identity = document.createElement("div");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = toolkit.label;
    const categoryLabel = document.createElement("div");
    categoryLabel.className = "category";
    categoryLabel.textContent = toolkit.recommended ? `Recomendada · ${toolkit.category}` : toolkit.category;
    identity.append(name, categoryLabel);
    head.append(identity);

    const description = document.createElement("p");
    description.className = "description";
    description.textContent = toolkit.description;

    const capabilities = document.createElement("ul");
    capabilities.className = "capabilities";
    for (const capability of toolkit.capabilities || []) {
      const item = document.createElement("li");
      item.textContent = capability;
      capabilities.append(item);
    }

    const foot = document.createElement("div");
    foot.className = "item-foot";
    const state = document.createElement("div");
    state.className = "state";
    const dot = document.createElement("i");
    dot.className = "dot";
    dot.setAttribute("aria-hidden", "true");
    const stateText = document.createElement("span");
    stateText.textContent = labels[toolkit.status] || toolkit.status;
    state.append(dot, stateText);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "connect";
    if (!toolkit.connectable) {
      button.textContent = "Lista para usar";
      button.disabled = true;
    } else {
      button.textContent = toolkit.status === "active" ? "Reconectar" : "Conectar";
      button.setAttribute("aria-label", `${button.textContent} ${toolkit.label}`);
    }
    foot.append(state, button);
    main.append(head, description, capabilities, foot);
    article.append(mark, main);
    return article;
  }

  function renderFilters() {
    const views = [
      ["recommended", "Recomendadas"],
      ["connected", "Conectadas"],
      ["all", "Todas"],
    ];
    filters.replaceChildren();
    for (const [value, label] of views) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "filter";
      button.textContent = label;
      button.dataset.view = value;
      button.setAttribute("aria-pressed", String(value === viewMode));
      filters.append(button);
    }
  }

  function render() {
    const query = search.value.trim();
    const matchesCatalog = catalog.filter((toolkit) => matches(toolkit, query));
    const visible = matchesCatalog.slice(0, visibleLimit);
    list.replaceChildren(...visible.map(createToolkitCard));
    empty.classList.toggle("show", matchesCatalog.length === 0);
    loadMore.classList.toggle("show", visible.length < matchesCatalog.length);
    resultsCount.textContent = visible.length < matchesCatalog.length
      ? `${visible.length} de ${matchesCatalog.length} integraciones`
      : `${matchesCatalog.length} ${matchesCatalog.length === 1 ? "integración" : "integraciones"}`;
    activeFilter.textContent = query ? "Resultados en todo el catálogo" : {
      recommended: "Recomendadas",
      connected: "Conectadas",
      all: "Todas",
    }[viewMode];
  }

  async function load({ force = false } = {}) {
    if (loading) return;
    if (!force && Date.now() - lastLoadedAt < 3000) return;
    loading = true;
    refresh.disabled = true;
    refresh.textContent = "Actualizando…";
    try {
      const response = await fetch("/api/status", { headers });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "No se pudo consultar el estado");
      catalog = data.toolkits;
      visibleLimit = PAGE_SIZE;
      lastLoadedAt = Date.now();
      renderFilters();
      render();
      document.getElementById("headline").textContent = `${data.active} de ${data.total} conectadas`;
      document.getElementById("subline").textContent = data.active
        ? "Tus fuentes conectadas están listas para Notebookvibes"
        : "Conecta una fuente para ampliar tus investigaciones";
    } catch (error) {
      toast(error.message, "error");
      document.getElementById("headline").textContent = "No se pudo actualizar";
      document.getElementById("subline").textContent = "Pulsa Actualizar para reintentar";
    } finally {
      loading = false;
      refresh.disabled = false;
      refresh.textContent = "Actualizar";
    }
  }

  async function connect(slug, button) {
    button.disabled = true;
    const old = button.textContent;
    button.textContent = "Generando…";
    try {
      const response = await fetch("/api/link", {
        method: "POST",
        headers,
        body: JSON.stringify({ toolkit: slug }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "No se pudo crear el enlace");
      toast("Abriendo autorización oficial…");
      if (tg?.openLink) tg.openLink(data.redirect_url);
      else window.open(data.redirect_url, "_blank", "noopener,noreferrer");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = old;
    }
  }

  list.addEventListener("click", (event) => {
    const button = event.target.closest(".connect");
    if (!button) return;
    connect(button.closest(".item").dataset.toolkit, button);
  });
  filters.addEventListener("click", (event) => {
    const button = event.target.closest(".filter");
    if (!button) return;
    viewMode = button.dataset.view;
    visibleLimit = PAGE_SIZE;
    for (const filter of filters.querySelectorAll(".filter")) {
      filter.setAttribute("aria-pressed", String(filter === button));
    }
    render();
  });
  search.addEventListener("input", () => {
    visibleLimit = PAGE_SIZE;
    render();
  });
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      search.value = "";
      visibleLimit = PAGE_SIZE;
      render();
    }
  });
  loadMore.addEventListener("click", () => {
    visibleLimit += PAGE_SIZE;
    render();
  });
  refresh.addEventListener("click", () => load({ force: true }));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  window.addEventListener("focus", () => load());
  load({ force: true });
})();
