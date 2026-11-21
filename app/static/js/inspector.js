/* Инспектор исходного кода элемента.
   Alt+B — режим «бэкенд», Alt+F — режим «фронтенд», Esc — выйти.
   Наведению соответствует подсветка, клику — карточка в левом верхнем углу
   с путями к файлам и диапазонами строк из /static/dev/source-map.json. */
(() => {
  "use strict";

  const MAP_URL = "/static/dev/source-map.json";
  const MODES = {
    backend: { title: "БЭКЕНД", key: "Alt+B", color: "backend" },
    frontend: { title: "ФРОНТЕНД", key: "Alt+F", color: "frontend" },
  };

  let map = null;
  let mode = null;
  let locked = null;
  let current = null;

  const ui = document.createElement("div");
  ui.className = "dev-ui-root";
  ui.innerHTML = `
    <div class="dev-badge dev-ui" hidden>
      <i class="dev-dot"></i><span class="dev-badge-text"></span>
      <span class="dev-badge-hint">Alt+B — бэкенд · Alt+F — фронтенд · Esc — выйти</span>
    </div>
    <div class="dev-highlight dev-ui" hidden><span class="dev-tag"></span></div>
    <aside class="dev-card dev-ui" hidden>
      <div class="dev-card-head">
        <div><p class="dev-card-kicker dev-card-kicker--backend">ИНСПЕКТОР КОДА</p><h2 class="dev-card-title"></h2></div>
        <button class="dev-close" type="button" aria-label="Закрыть карточку">×</button>
      </div>
      <p class="dev-selector"></p>
      <div class="dev-sections"></div>
    </aside>`;
  document.body.append(ui);

  const badge = ui.querySelector(".dev-badge");
  const badgeText = ui.querySelector(".dev-badge-text");
  const highlight = ui.querySelector(".dev-highlight");
  const tag = ui.querySelector(".dev-tag");
  const card = ui.querySelector(".dev-card");
  const sections = ui.querySelector(".dev-sections");

  function loadMap() {
    if (map) return Promise.resolve(map);
    return fetch(MAP_URL, { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        map = data.entries || {};
        badge.classList.add("dev-ready");
        return map;
      })
      .catch((err) => {
        badgeText.textContent = `Карта кода не загрузилась (${err.message})`;
        badge.classList.add("dev-error");
        return null;
      });
  }

  function setMode(next) {
    mode = mode === next ? null : next;
    document.body.classList.toggle("dev-inspect", Boolean(mode));
    badge.hidden = !mode;
    highlight.hidden = !mode;
    if (!mode) {
      closeCard();
      current = null;
      return;
    }
    highlight.classList.toggle("dev-backend", mode === "backend");
    highlight.classList.toggle("dev-frontend", mode === "frontend");
    badgeText.textContent = `РЕЖИМ: ${MODES[mode].title}`;
    badge.classList.remove("dev-ready", "dev-error");
    badge.classList.toggle("dev-frontend", mode === "frontend");
    // карточка уже открыта — показываем раздел нового режима
    if (!card.hidden && current) renderCard(current);
    loadMap();
  }

  function closeCard() {
    card.hidden = true;
    locked = null;
  }

  function isSmallLeaf(element) {
    const rect = element.getBoundingClientRect();
    return element.children.length === 0 && rect.width > 0 && rect.width < 400 && rect.height < 120;
  }

  function resolve(element) {
    if (!map || !element || element.nodeType !== 1) return null;
    let node = element;
    while (node) {
      // matches() понимает и простые (#id, .class), и составные селекторы
      // (.level-btn > b, .qa-opt span) — так мелкие элементы выделяются сами.
      for (const key of Object.keys(map)) {
        try {
          if (node.matches(key)) return { el: node, key, data: map[key] };
        } catch { /* невалидный селектор пропускаем */ }
      }
      // Мелкий элемент без записи в карте не «раздуваем» до большой панели —
      // подсвечиваем его самого, даже если только что добавлять запись не хочется.
      if (node === element && isSmallLeaf(node)) return rawFound(node);
      if (node === document.body || node === document.documentElement) break;
      node = node.parentElement;
    }
    return null;
  }

  function rawFound(element) {
    const id = element.id ? "#" + element.id : "";
    const cls = element.classList.length ? "." + [...element.classList].join(".") : "";
    return {
      el: element,
      key: `<${element.tagName.toLowerCase()}${id}${cls}>`,
      data: { label: "Элемент без записи в карте кода", backend: [], frontend: [], raw: true },
    };
  }

  function place(found) {
    const rect = found.el.getBoundingClientRect();
    highlight.style.left = `${rect.left}px`;
    highlight.style.top = `${rect.top}px`;
    highlight.style.width = `${Math.max(rect.width, 2)}px`;
    highlight.style.height = `${Math.max(rect.height, 2)}px`;
    highlight.classList.toggle("dev-unknown", Boolean(found.data.raw));
    tag.textContent = `${MODES[mode].title} · ${found.data.label} · ${found.key}`;
  }

  function locationLine(item) {
    const row = document.createElement("div");
    row.className = "dev-location";

    const path = document.createElement("div");
    path.className = "dev-path";
    path.textContent = `${item.dir}${item.name}`;

    const meta = document.createElement("div");
    meta.className = "dev-meta";
    meta.textContent = item.start === item.end
      ? `строка ${item.start}`
      : `строки ${item.start}–${item.end}`;

    const note = document.createElement("div");
    note.className = "dev-note";
    note.textContent = item.note || "";

    row.append(path, meta, note);
    return row;
  }

  function renderCard(found) {
    const kicker = card.querySelector(".dev-card-kicker");
    kicker.textContent = `ИНСПЕКТОР КОДА · ${MODES[mode].title}`;
    kicker.classList.toggle("dev-card-kicker--frontend", mode === "frontend");
    card.querySelector(".dev-card-title").textContent = found.data.label;
    card.querySelector(".dev-selector").textContent = `элемент: ${found.key} · <${found.el.tagName.toLowerCase()}>`;
    sections.replaceChildren();

    // показываем только тот режим, который включён
    const block = document.createElement("section");
    block.className = `dev-section dev-section--${mode}`;
    const heading = document.createElement("h3");
    heading.textContent = mode === "backend"
      ? "Бэкенд — где обрабатывается"
      : "Фронтенд — где отрисовывается";
    block.append(heading);

    const items = found.data[mode] || [];
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "dev-empty";
      empty.textContent = found.data.raw
        ? "Нет записи в карте кода — пересоберите source-map.json"
        : "Для этого элемента не привязано";
      block.append(empty);
    } else {
      items.forEach((item) => block.append(locationLine(item)));
    }
    sections.append(block);
    card.hidden = false;
  }

  function isOwn(target) {
    return target instanceof Element && Boolean(target.closest(".dev-ui"));
  }

  // Клик по карточке (кроме её кнопки закрытия) считаем кликом «сквозь» неё:
  // так картинка в левом верхнем углу не мешает выбирать элементы под ней.
  function pickTarget(event) {
    if (!isOwn(event.target)) return event.target;
    if (event.target.closest(".dev-close")) return null;
    return document.elementsFromPoint(event.clientX, event.clientY)
      .find((el) => !isOwn(el)) || null;
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && mode) {
      event.preventDefault();
      setMode(null);
      return;
    }
    if (!event.altKey || event.ctrlKey || event.metaKey) return;
    // code — физическая клавиша (работает при любой раскладке), key — запасной вариант
    const key = (event.key || "").toLowerCase();
    const isBackend = event.code === "KeyB" || key === "b" || key === "и";
    const isFrontend = event.code === "KeyF" || key === "f" || key === "а";
    if (isBackend) {
      event.preventDefault();
      setMode("backend");
    } else if (isFrontend) {
      event.preventDefault();
      setMode("frontend");
    }
  }, true);

  document.addEventListener("mousemove", (event) => {
    if (!mode || locked || isOwn(event.target)) return;
    const found = resolve(event.target) || rawFound(event.target);
    current = found;
    place(found);
  }, true);

  // В режиме инспекции не даём клику срабатывать как обычному действию сайта
  for (const type of ["mousedown", "click"]) {
    document.addEventListener(type, (event) => {
      if (!mode) return;
      if (isOwn(event.target) && type === "mousedown") return;
      const target = pickTarget(event);
      if (!target) return;
      event.preventDefault();
      event.stopPropagation();
      const found = resolve(target) || rawFound(target);
      locked = found.el;
      current = found;
      place(found);
      // Карточку показываем после завершения текущего цикла клика:
      // иначе она появляется под курсором mouseup и «съедает» этот же клик.
      setTimeout(() => {
        if (mode && locked === found.el) renderCard(found);
      }, 0);
    }, true);
  }

  ui.querySelector(".dev-close").addEventListener("click", () => {
    closeCard();
  });

  addEventListener("scroll", () => {
    if (locked && current && mode) place(current);
  }, { passive: true });
  addEventListener("resize", () => {
    if (locked && current && mode) place(current);
  });
})();
