(() => {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
  }

  const token = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const headers = { "Content-Type": "application/json", "X-Miniapp-Token": token };
  const telegramHeaders = {
    ...headers,
    "X-Telegram-Init-Data": tg?.initData || "",
  };
  const labels = {
    active: "Conectado",
    connected: "Conectado",
    pending: "Autorización pendiente",
    expired: "Sesión caducada",
    disconnected: "Sin conectar",
    needs_reconnection: "Necesita reconexión",
    available: "Disponible sin conexión",
  };
  const platformDescriptions = {
    instagram: "Publicaciones, Reels, comentarios y analítica.",
    facebook: "Páginas, contenido, comentarios e insights.",
    linkedin: "Perfil u organización profesional.",
    tiktok: "Videos, comentarios, inbox y analítica.",
    youtube: "Videos, Shorts, comentarios y métricas.",
    twitter: "Publicaciones, respuestas y conversaciones en X.",
    threads: "Publicaciones y respuestas en Threads.",
    telegram: "Canal o bot mediante un código temporal.",
    whatsapp: "WhatsApp Business mediante Meta Embedded Signup.",
  };
  const platformMarks = {
    instagram: "IG", facebook: "FB", linkedin: "in", tiktok: "TT",
    youtube: "YT", twitter: "X", threads: "@", telegram: "TG", whatsapp: "WA",
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
  const zernioList = document.getElementById("zernio-list");
  const zernioEmpty = document.getElementById("zernio-empty");
  const zernioRefresh = document.getElementById("zernio-refresh");
  const telegramDialog = document.getElementById("telegram-dialog");
  const telegramCode = document.getElementById("telegram-code");
  const telegramBotLink = document.getElementById("telegram-bot-link");
  const telegramCheck = document.getElementById("telegram-check");

  let catalog = [];
  let zernioPlatforms = [];
  let viewMode = "recommended";
  let activeModule = "composio";
  let visibleLimit = PAGE_SIZE;
  let lastLoadedAt = 0;
  let zernioLoadedAt = 0;
  let loading = false;
  let zernioLoading = false;
  let activeTelegramCode = "";
  let toastTimer;

  function toast(text, type = "ok") {
    window.clearTimeout(toastTimer);
    msg.textContent = text;
    msg.className = `message show ${type}`;
    toastTimer = window.setTimeout(() => { msg.className = "message"; }, 4500);
  }

  function publicError(code) {
    return {
      telegram_auth_required: "Abre esta Mini App desde el bot autorizado de Telegram.",
      zernio_not_configured: "Zernio todavía no está configurado en el servidor.",
      zernio_status_failed: "No pudimos consultar Zernio. Inténtalo de nuevo.",
      zernio_request_failed: "Zernio no pudo iniciar la conexión. Inténtalo de nuevo.",
      invalid_integration_request: "La solicitud de conexión no es válida.",
    }[code] || "No se pudo completar la operación.";
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
    const haystack = [toolkit.label, toolkit.category, toolkit.description,
      ...(toolkit.capabilities || []), ...(toolkit.search_terms || [])].join(" ");
    return normalized(haystack).includes(normalized(query));
  }

  function stateElement(status, username = "") {
    const state = document.createElement("div");
    state.className = "state";
    const dot = document.createElement("i");
    dot.className = "dot";
    dot.setAttribute("aria-hidden", "true");
    const stateText = document.createElement("span");
    stateText.textContent = username && status === "connected"
      ? `${labels[status]} · ${username}`
      : (labels[status] || status);
    state.append(dot, stateText);
    return state;
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
    foot.append(stateElement(toolkit.status), button);
    main.append(head, description, capabilities, foot);
    article.append(mark, main);
    return article;
  }

  function createZernioCard(platform) {
    const article = document.createElement("article");
    article.className = "item";
    article.dataset.platform = platform.platform;
    article.dataset.state = platform.status;
    const mark = document.createElement("div");
    mark.className = "mark";
    mark.textContent = platformMarks[platform.platform] || platform.label.slice(0, 2).toUpperCase();
    mark.setAttribute("aria-hidden", "true");
    const main = document.createElement("div");
    main.className = "item-main";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = platform.label;
    const category = document.createElement("div");
    category.className = "category";
    category.textContent = `${platform.category} · Zernio`;
    const description = document.createElement("p");
    description.className = "description";
    description.textContent = platformDescriptions[platform.platform] || "Conecta esta red mediante Zernio.";
    const foot = document.createElement("div");
    foot.className = "item-foot";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "connect";
    button.textContent = platform.status === "connected" ? "Reconectar" :
      platform.status === "needs_reconnection" ? "Reparar" : "Conectar";
    button.setAttribute("aria-label", `${button.textContent} ${platform.label}`);
    foot.append(stateElement(platform.status, platform.username), button);
    main.append(name, category, description, foot);
    article.append(mark, main);
    return article;
  }

  function renderFilters() {
    const views = [["recommended", "Recomendadas"], ["connected", "Conectadas"], ["all", "Todas"]];
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
    activeFilter.textContent = query ? "Resultados en todo el catálogo" :
      ({ recommended: "Recomendadas", connected: "Conectadas", all: "Todas" }[viewMode]);
  }

  function renderZernio() {
    zernioList.replaceChildren(...zernioPlatforms.map(createZernioCard));
    zernioEmpty.classList.toggle("show", zernioPlatforms.length === 0);
    const connected = zernioPlatforms.filter((item) => item.status === "connected").length;
    document.getElementById("zernio-headline").textContent = `${connected} de ${zernioPlatforms.length} conectadas`;
    document.getElementById("zernio-subline").textContent = connected
      ? "Tus redes conectadas están listas en Zernio"
      : "Conecta tu primera red social";
  }

  async function load({ force = false } = {}) {
    if (loading || (!force && Date.now() - lastLoadedAt < 3000)) return;
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

  async function loadZernio({ force = false } = {}) {
    if (zernioLoading || (!force && Date.now() - zernioLoadedAt < 3000)) return;
    zernioLoading = true;
    zernioRefresh.disabled = true;
    zernioRefresh.textContent = "Actualizando…";
    try {
      const response = await fetch("/api/zernio/status", { headers: telegramHeaders });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(publicError(data.error));
      zernioPlatforms = data.platforms || [];
      zernioLoadedAt = Date.now();
      renderZernio();
    } catch (error) {
      zernioPlatforms = [];
      renderZernio();
      document.getElementById("zernio-headline").textContent = "No se pudo actualizar";
      document.getElementById("zernio-subline").textContent = error.message;
      toast(error.message, "error");
    } finally {
      zernioLoading = false;
      zernioRefresh.disabled = false;
      zernioRefresh.textContent = "Actualizar";
    }
  }

  async function connectComposio(slug, button) {
    button.disabled = true;
    const old = button.textContent;
    button.textContent = "Generando…";
    try {
      const response = await fetch("/api/link", { method: "POST", headers, body: JSON.stringify({ toolkit: slug }) });
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

  async function startTelegram(button) {
    button.disabled = true;
    const old = button.textContent;
    button.textContent = "Generando…";
    try {
      const response = await fetch("/api/zernio/telegram/start", {
        method: "POST", headers: telegramHeaders, body: "{}",
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(publicError(data.error));
      activeTelegramCode = data.code;
      telegramCode.textContent = data.code;
      if (data.botUsername) telegramBotLink.href = `https://t.me/${encodeURIComponent(data.botUsername)}`;
      telegramDialog.showModal();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = old;
    }
  }

  async function connectZernio(platform, button) {
    if (platform.platform === "telegram") {
      await startTelegram(button);
      return;
    }
    if (platform.platform === "tiktok" && platform.status === "connected") {
      const accepted = window.confirm("Reconectar TikTok puede reemplazar la cuenta y borrar historial previo de analítica e inbox. ¿Continuar?");
      if (!accepted) return;
    }
    button.disabled = true;
    const old = button.textContent;
    button.textContent = "Generando…";
    try {
      const response = await fetch("/api/zernio/connect", {
        method: "POST", headers: telegramHeaders, body: JSON.stringify({ platform: platform.platform }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(publicError(data.error));
      toast("Abriendo autorización oficial de Zernio…");
      if (tg?.openLink) tg.openLink(data.authUrl);
      else window.open(data.authUrl, "_blank", "noopener,noreferrer");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = old;
    }
  }

  function selectModule(module, { updateUrl = true } = {}) {
    activeModule = module === "zernio" ? "zernio" : "composio";
    for (const button of document.querySelectorAll(".module-tab")) {
      const selected = button.dataset.module === activeModule;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      document.getElementById(button.getAttribute("aria-controls")).hidden = !selected;
    }
    if (updateUrl) {
      const url = new URL(location.href);
      url.searchParams.set("module", activeModule);
      history.pushState({ module: activeModule }, "", url);
    }
    if (activeModule === "zernio") loadZernio();
    else load();
  }

  list.addEventListener("click", (event) => {
    const button = event.target.closest(".connect");
    if (button) connectComposio(button.closest(".item").dataset.toolkit, button);
  });
  zernioList.addEventListener("click", (event) => {
    const button = event.target.closest(".connect");
    if (!button) return;
    const platform = zernioPlatforms.find((item) => item.platform === button.closest(".item").dataset.platform);
    if (platform) connectZernio(platform, button);
  });
  filters.addEventListener("click", (event) => {
    const button = event.target.closest(".filter");
    if (!button) return;
    viewMode = button.dataset.view;
    visibleLimit = PAGE_SIZE;
    for (const filter of filters.querySelectorAll(".filter")) filter.setAttribute("aria-pressed", String(filter === button));
    render();
  });
  const moduleTabs = document.querySelector(".module-tabs");
  moduleTabs.addEventListener("click", (event) => {
    const button = event.target.closest(".module-tab");
    if (button) selectModule(button.dataset.module);
  });
  moduleTabs.addEventListener("keydown", (event) => {
    const tabs = [...moduleTabs.querySelectorAll(".module-tab")];
    const currentIndex = tabs.indexOf(event.target.closest(".module-tab"));
    if (currentIndex < 0) return;
    let nextIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = tabs.length - 1;
    else return;
    event.preventDefault();
    const nextTab = tabs[nextIndex];
    selectModule(nextTab.dataset.module);
    nextTab.focus();
  });
  search.addEventListener("input", () => { visibleLimit = PAGE_SIZE; render(); });
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { search.value = ""; visibleLimit = PAGE_SIZE; render(); }
  });
  loadMore.addEventListener("click", () => { visibleLimit += PAGE_SIZE; render(); });
  refresh.addEventListener("click", () => load({ force: true }));
  zernioRefresh.addEventListener("click", () => loadZernio({ force: true }));
  document.getElementById("telegram-close").addEventListener("click", () => telegramDialog.close());
  telegramCheck.addEventListener("click", async () => {
    telegramCheck.disabled = true;
    telegramCheck.textContent = "Comprobando…";
    try {
      const response = await fetch("/api/zernio/telegram/check", {
        method: "POST", headers: telegramHeaders, body: JSON.stringify({ code: activeTelegramCode }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(publicError(data.error));
      if (data.status === "pending") throw new Error("Aún no vemos la conexión. Envía el código al bot y reintenta.");
      if (data.status === "expired") throw new Error("El código venció. Cierra esta ventana y genera uno nuevo.");
      if (data.status !== "connected") throw new Error("Zernio devolvió un estado desconocido. Reintenta.");
      telegramDialog.close();
      toast("Telegram quedó conectado.");
      await loadZernio({ force: true });
    } catch (error) {
      toast(error.message, "error");
    } finally {
      telegramCheck.disabled = false;
      telegramCheck.textContent = "Ya envié el código";
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) activeModule === "zernio" ? loadZernio() : load();
  });
  window.addEventListener("focus", () => activeModule === "zernio" ? loadZernio() : load());
  window.addEventListener("popstate", () => selectModule(new URL(location.href).searchParams.get("module"), { updateUrl: false }));

  renderFilters();
  selectModule(new URL(location.href).searchParams.get("module"), { updateUrl: false });
})();
