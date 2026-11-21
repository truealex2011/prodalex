import { CarViewer, TOUR } from "./viewer.js";
import { MooseScene } from "./moose2d.js";
import { MooseTest } from "./moose3d.js";

const $ = (s) => document.querySelector(s);
const state = { sid: null, question: null, rec: null, color: null, user: null, excluded: [] };
// token растёт при каждой смене экрана, run — при каждой новой анкете: устаревшие ответы сервера и нейросети выбрасываются
const nav = { token: 0, run: 0 };
let _sid = null;
try { _sid = localStorage.getItem("ac_sid"); } catch { /* приватный режим — просто без восстановления */ }
Object.defineProperty(state, "sid", {
  enumerable: true,
  get: () => _sid,
  set(v) {
    _sid = v;
    try { v ? localStorage.setItem("ac_sid", v) : localStorage.removeItem("ac_sid"); } catch { /* ignore */ }
  },
});

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const money = (v) => (v ? `${v.toLocaleString("ru-RU")} ₽` : "нет данных");

// 3D создаём сразу: город (~10 МБ) грузится, пока человек отвечает на вопросы
const viewer = new CarViewer($("#viewer"), { callouts: $("#callouts"), lines: $("#callout-lines") });
if (location.search.includes("debug")) window.viewer = viewer; // отладка 3D из консоли

async function api(method, path, body) {
  const res = await fetch(`/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error?.message || "Сервер недоступен, попробуйте ещё раз");
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), 3500);
}

// адрес в строке браузера = экран; кнопки «назад/вперёд» работают, ссылки открываются сразу на нужном экране
const SCREEN_PATH = { "screen-mode": "/", "screen-pick": "/start", "screen-describe": "/describe", "screen-question": "/question", "screen-empty": "/empty" };
function pathFor(id) {
  if (id === "screen-account") return `/account/${accTab}`;
  if (id === "screen-result") return !state.rec?.car ? null : state.rec.car.market ? "/result" : `/car/${state.rec.car.id}`;
  return SCREEN_PATH[id] ?? null;
}
function syncPath(id) {
  const p = pathFor(id);
  if (p && location.pathname !== p) history.pushState({}, "", p + location.search);
}

function show(id, { push = true } = {}) {
  nav.token++;
  if (id !== "screen-question") nav.run++;         // ушли с анкеты: ответы нейросети, которые ещё в пути, больше не нужны
  document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("active", s.id === id));
  $("#stage").hidden = id !== "screen-result";
  document.documentElement.classList.toggle("no-scrollbar", id === "screen-result");
  if (id !== "screen-result") closeMoose();
  closeSheet();
  window.scrollTo({ top: 0, behavior: "instant" });
  if (push) syncPath(id);
}

async function guarded(fn) {
  try { await fn(); } catch (e) { toast(e.message); }
}

// новый поиск начинается с чистого состояния: анкета, прошлая выдача и исключённые модели не тянутся из прошлого
function resetSearch() {
  Object.assign(form, { sid: null, questions: new Map(), answers: new Map(), sent: new Set(), finishing: 0 });
  state.rec = null;
  state.excluded = [];
  state.goal = undefined;
  try { sessionStorage.removeItem("ac_rec"); } catch { /* без хранилища — просто не восстановим при перезагрузке */ }
}
function saveRec() {
  try { sessionStorage.setItem("ac_rec", JSON.stringify({ sid: state.sid, rec: state.rec })); } catch { /* слишком большой или хранилище закрыто */ }
}
function loadRec() {
  try {
    const v = JSON.parse(sessionStorage.getItem("ac_rec") || "null");
    return v && v.sid === state.sid ? v.rec : null;
  } catch { return null; }
}
const refetchRec = () => state.rec?.source === "market"
  ? api("GET", `/sessions/${state.sid}/market?exclude=${encodeURIComponent((state.excluded || []).join(","))}`)
  : api("GET", `/sessions/${state.sid}/recommendation`);

// 1. Режим
document.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => guarded(async () => {
  if (!state.user) return startSearch();
  const s = await api("POST", "/sessions", { mode: b.dataset.mode });
  state.sid = s.session_id;
  resetSearch();
  if (state.user && !state.user.mode) refreshUser(); // выбор режима запомнен в аккаунте
  show("screen-describe");
  $("#describe-text").focus();
})));

// 2. Описание
$("#hint-chips").addEventListener("click", (e) => {
  if (!e.target.classList.contains("chip")) return;
  const ta = $("#describe-text");
  ta.value = ta.value ? `${ta.value.trim()}, ${e.target.textContent}` : e.target.textContent;
});
$("#describe-go").addEventListener("click", () => guarded(async () => {
  const text = $("#describe-text").value.trim();
  if (!text) return toast("Напишите хотя бы пару слов или нажмите «Не знаю»");
  const btn = $("#describe-go"), label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Нейросеть анализирует запрос...";  // локальная модель думает несколько секунд
  let r;
  try {
    r = await api("POST", `/sessions/${state.sid}/describe`, { text });
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
  state.color = r.profile.color;
  state.goal = r.profile.goal || null;
  await nextQuestion();
}));
$("#describe-skip").addEventListener("click", () => guarded(nextQuestion));

// 3. Анкета: вопросы формулирует нейросеть (как в «парсинке»), варианты ответов фиксированы
// «Я понял так…»: пересказ желания нейросетью (если она установлена)
function showGoal() {
  for (const sel of ["#q-goal", "#loading-goal", "#sheet-goal"]) {
    const el = $(sel);
    el.hidden = !state.goal;
    el.textContent = state.goal ? `Я понял так: ${state.goal}` : "";
  }
}

const form = { sid: null, questions: new Map(), answers: new Map(), sent: new Set(), busy: false, timer: null, retry: "initial", finishing: 0, finishSeq: 0 };

function qaStatus(hint = "") {
  const total = form.questions.size, done = [...form.questions.keys()].filter((k) => form.answers.has(k)).length;
  $("#qa-status-text").textContent = hint || (total ? `Отвечено: ${done} из ${total}.` : "");
  $("#qa-bar").style.width = total ? `${(done / total) * 100}%` : "0";
}
function qaLock(busy, hint = "") {
  form.busy = busy;
  $("#qa-list").inert = busy;
  $("#qa-finish").disabled = busy;
  $("#qa-wait").hidden = !busy;
  qaStatus(hint);
  qaMoreVisibility();
}
// «Уточнить ещё» — только по желанию клиента: анкета больше не дописывается сама, пока он готовится нажать «Показать машину»
function qaMoreVisibility() {
  $("#qa-more").hidden = form.busy || !form.questions.size || ![...form.questions.keys()].every((k) => form.answers.has(k));
}
async function openForm() {
  const run = ++nav.run;
  Object.assign(form, { sid: state.sid, questions: new Map(), answers: new Map(), sent: new Set(), retry: "initial", finishing: 0 });
  clearTimeout(form.timer);
  $("#qa-list").replaceChildren();
  $("#qa-more").hidden = true;
  const st = await api("GET", `/sessions/${state.sid}/profile`).catch(() => ({ profile: {}, chips: [] }));
  if (run !== nav.run) return;
  state.goal = st.profile.goal || null;
  showGoal();
  $("#qa-known").innerHTML = st.chips?.length ? `<span class="qa-known-title">Из вашего описания:</span>${st.chips.map((c) => `<span class="chip${c.must ? " must" : ""}">${esc(c.label)}</span>`).join("")}` : "";
  show("screen-question");
  await qaAsk("initial", run);
}
async function qaSend() {
  const batch = [...form.answers.values()].filter((a) => !form.sent.has(a.slot));
  if (!batch.length) return;
  await api("POST", `/sessions/${state.sid}/answers`, { answers: batch });
  batch.forEach((a) => { form.sent.add(a.slot); $(`.qa-card[data-slot="${a.slot}"]`)?.setAttribute("disabled", ""); });
}
async function qaAsk(phase, run = nav.run) {
  clearTimeout(form.timer);
  $("#qa-retry").hidden = true;
  $("#qa-more").hidden = true;
  qaLock(true, phase === "initial" ? "Ассистент составляет анкету..." : "Проверяю, нужны ли уточнения...");
  try {
    await qaSend();
    const { questions } = await api("GET", `/sessions/${state.sid}/advisor?phase=${phase}`);
    if (run !== nav.run) return;                  // клиент ушёл с анкеты или начал заново: этот ответ уже не нужен
    const fresh = questions.filter((q) => !form.questions.has(q.slot));
    if (!fresh.length) {                          // данных достаточно
      if (phase === "more") { qaLock(false); return toast("Больше уточнений не нужно — можно показывать машину"); }
      return await qaFinish();
    }
    let first;
    for (const q of fresh) {
      form.questions.set(q.slot, q);
      const card = qaCard(q, form.questions.size);
      $("#qa-list").append(card);
      first ??= card;
    }
    qaLock(false);
    if (phase === "more") first.scrollIntoView({ block: "center", behavior: "smooth" });
  } catch (e) {
    if (run !== nav.run) return;
    form.retry = phase;
    qaLock(false, "Не удалось получить вопросы. Ответы сохранены.");
    $("#qa-retry").hidden = false;
  }
}
async function qaFinish() {
  if (form.finishing) return;                     // повторное нажатие не запускает второй поиск
  const id = (form.finishing = ++form.finishSeq);
  clearTimeout(form.timer);
  qaLock(true, "Подбираю машину...");
  try {
    await qaSend();
    await showResult(api("GET", `/sessions/${state.sid}/market`));
  } finally {
    if (form.finishing === id) {
      form.finishing = 0;
      qaLock(false);
    }
  }
}
function qaCard(q, n) {
  const name = `qa-${q.slot}`;
  const row = (value, label, extra = "") => `<label class="qa-opt"><input type="radio" name="${name}" value="${value}"><span>${esc(label)}</span></label>${extra}`;
  const el = document.createElement("fieldset");
  el.className = "qa-card";
  el.dataset.slot = q.slot;
  el.style.setProperty("--i", n);
  el.innerHTML = `<legend><span class="q-number">${String(n).padStart(2, "0")}</span>${esc(q.text)}</legend>
    <div class="qa-opts">${q.options.map((o, i) => row(i, o.label)).join("")}${row("__own__", "Свой ответ")}${row("__skip__", "Не важно")}</div>
    <input class="qa-own" maxlength="160" placeholder="Ваш вариант" aria-label="Свой ответ" hidden>
    <label class="qa-must"><input type="checkbox" role="switch" ${q.default_must ? "checked" : ""}><span class="sw" aria-hidden="true"></span>Обязательно</label>`;
  return el;
}
function qaSave(q, answer) {
  form.answers.set(q.slot, { slot: q.slot, ...answer });
  qaStatus();
  qaMoreVisibility();
}
function qaRead(card) {
  const q = form.questions.get(card.dataset.slot), radio = card.querySelector("input[type=radio]:checked");
  if (!radio) return;
  const own = card.querySelector(".qa-own"), must = card.querySelector(".qa-must input");
  own.hidden = radio.value !== "__own__";
  must.disabled = radio.value === "__skip__";
  if (radio.value === "__own__") {
    if (own.value.trim()) qaSave(q, { custom_text: own.value.trim() });
    else own.focus();
  } else if (radio.value === "__skip__") qaSave(q, { value: "any" });
  else qaSave(q, { value: q.options[+radio.value].value, must: must.checked });
}
$("#qa-list").addEventListener("change", (e) => { const c = e.target.closest(".qa-card"); if (c && !e.target.classList.contains("qa-own")) qaRead(c); });
$("#qa-list").addEventListener("focusout", (e) => { if (e.target.classList.contains("qa-own") && e.target.value.trim()) qaRead(e.target.closest(".qa-card")); });
$("#qa-list").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.classList.contains("qa-own")) { e.preventDefault(); e.target.blur(); } });
$("#qa-finish").addEventListener("click", () => guarded(qaFinish));
$("#qa-more").addEventListener("click", () => guarded(() => qaAsk("more")));
$("#load-cancel").addEventListener("click", () => {
  form.finishing = 0;
  qaLock(false);
  show(form.sid === state.sid && form.questions.size ? "screen-question" : "screen-describe");
});
$("#qa-retry").addEventListener("click", () => qaAsk(form.retry));
const nextQuestion = openForm;   // прежнее имя: его вызывают описание и восстановление экрана по адресу

// 4. Подбор: анимация, пока считается выдача и грузится 3D
const LOADING = ["Нейросеть подбирает модели под ваш запрос...", "Ищем живые объявления на Drom и FRESH...", "Сверяем цены и характеристики...", "Читаем ваши пожелания...", "Отсеиваем то, что не подходит...", "Взвешиваем компромиссы...", "Ищем 3D-модель на Sketchfab...", "Выкатываем машину на улицу..."];
async function showResult(promise, { instant = false } = {}) {
  show("screen-loading");
  const mine = nav.token;                         // если клиент уйдёт с экрана загрузки, результат ему уже не показываем
  (async () => {  // «Я понял так…» на экране загрузки
    if (state.goal === undefined) state.goal = (await api("GET", `/sessions/${state.sid}/profile`).catch(() => ({ profile: {} }))).profile.goal || null;
    showGoal();
  })();
  let i = 0;
  const steps = document.querySelectorAll("#load-steps i");
  const timer = setInterval(() => {
    i++;
    $("#loading-text").textContent = LOADING[i % LOADING.length];
    steps.forEach((d, k) => d.classList.toggle("on", k <= i % steps.length));
  }, 700);
  try {
    const [rec] = await Promise.all([promise, instant ? null : sleep(1800)]);
    if (mine !== nav.token) return;
    state.rec = rec;
    if (rec.notice) toast(rec.notice);
    if (rec.empty) {
      renderEmpty(rec);
      show("screen-empty");
      return;
    }
    const c = rec.car;
    const color = state.color && c.colors.includes(state.color) ? state.color : c.colors[0];
    rec.car = await prepareCar(c, color);
    if (mine !== nav.token) return;
    renderDetails(rec);
    applyTrack();
    show("screen-result");
    viewer.start();
    onScroll();
    saveRec();
  } catch (e) {
    if (mine !== nav.token) return;
    toast(e.message);
    show(form.sid === state.sid && form.questions.size ? "screen-question" : "screen-describe");   // ответы анкеты остаются на месте
  } finally {
    clearInterval(timer);
  }
}

// 4b. Что показать вместо/вместе с 3D: файл модели (наша или найденная на Sketchfab) либо фото
const gallery = {
  items: [], i: 0, active: false,
  set(photos) {
    this.items = photos || [];
    this.i = 0;
    this.active = false;                       // сцена 3D показывается всегда; фото открываются кнопкой «Фото машины»
    $("#stage").classList.remove("photo-mode");
    $("#photo-stage").hidden = true;
    $("#btn-photos").hidden = !this.items.length;
    $("#btn-photos").textContent = "Фото машины";
    if (!this.items.length) return;
    $("#photo-thumbs").innerHTML = this.items.map((p, k) => `<button type="button" data-k="${k}" aria-label="Фото ${k + 1}"><img src="${esc(p.url)}" alt="" loading="lazy"></button>`).join("");
    this.render();
  },
  render() {
    const p = this.items[this.i];
    const img = $("#photo-main");
    img.src = p.url;
    img.alt = p.title || "";
    $("#photo-stage").style.setProperty("--bg-img", `url("${p.url}")`);
    $("#photo-credit").innerHTML = `${esc(p.credit)}${p.page ? ` · <a href="${esc(p.page)}" target="_blank" rel="noopener">источник ↗</a>` : ""}`;
    document.querySelectorAll("#photo-thumbs button").forEach((b, k) => b.classList.toggle("on", k === this.i));
    $("#photo-prev").hidden = $("#photo-next").hidden = this.items.length < 2;
  },
  toggle() {
    this.active = !this.active;
    $("#stage").classList.toggle("photo-mode", this.active);
    $("#photo-stage").hidden = !this.active;
    $("#btn-photos").textContent = this.active ? "Вернуться к 3D" : "Фото машины";
    applyTrack();
  },
  go(d) { this.i = (this.i + d + this.items.length) % this.items.length; this.render(); },
};
$("#btn-photos").addEventListener("click", () => gallery.toggle());
$("#photo-prev").addEventListener("click", () => gallery.go(-1));
$("#photo-next").addEventListener("click", () => gallery.go(1));
$("#photo-thumbs").addEventListener("click", (e) => { const b = e.target.closest("[data-k]"); if (b) { gallery.i = +b.dataset.k; gallery.render(); } });
addEventListener("keydown", (e) => { if (gallery.active && !$("#stage").hidden && !$("#sheet").classList.contains("open")) { if (e.key === "ArrowLeft") gallery.go(-1); if (e.key === "ArrowRight") gallery.go(1); } });

/** Готовит машину к показу: берёт файл 3D-модели (если своей нет — ищет на Sketchfab и скачивает) или фото. */
async function prepareCar(car, color) {
  let media = { kind: car.model_url ? "model" : "none" };
  if (car.market && !car.model_url) {            // машина из объявления: 3D ищется по её названию и разбивается на детали
    const a = car.asset || {};
    const q = new URLSearchParams({ name: car.title, year: a.year || car.year || "", body: car.body || "", length: car.specs?.length_mm || "" });
    const m = await api("GET", `/model3d?${q}`).catch(() => ({ kind: "none" }));
    if (m.kind === "model") car = { ...car, model_url: m.url, parts_url: m.parts_url, model_credit: (m.similar ? `похожая модель (${m.similar}) — точной 3D этой машины нет в открытом доступе; ` : "") + m.credit, specs: { ...car.specs, measured: m.similar ? null : m.measured } };
    await viewer.prepare(car, color);
    const photos = (car.images || []).filter((u) => !/ttn|gen272/.test(u)).slice(0, 8);
    gallery.set((photos.length ? photos : [car.image_url].filter(Boolean)).map((u) => ({ url: u, credit: `Фото из объявления (${car.source || "Drom"})`, page: car.listing_url })));
    if (!car.model_url && gallery.items.length) {   // качественной 3D этой машины нет: сразу реальные фото, без заглушки
      gallery.toggle();
      $("#btn-photos").hidden = true;
    }
    return car;
  }
  if (!car.model_url) media = await api("GET", `/cars/${car.id}/media`).catch(() => ({ kind: "none" }));
  if (media.kind === "model" && !car.model_url) car = { ...car, model_url: media.model.url, parts_url: media.model.parts_url, model_credit: media.model.credit };
  await viewer.prepare(car, color);   // без модели — простая заглушка: она нужна тесту на лося
  gallery.set(media.kind === "photos" ? media.photos : null);
  return car;
}
const applyTrack = () => ($("#tour-track").style.height = gallery.active ? "100vh" : `${(TOUR.length - 1) * 70 + 100}vh`);

// 5. Экскурсия: прокрутка страницы = движение камеры
function onScroll() {
  if ($("#stage").hidden) return;
  const max = document.documentElement.scrollHeight - innerHeight;
  const p = max > 0 ? (scrollY / max) * (TOUR.length - 1) : 0;
  viewer.setProgress(p);
  const i = Math.round(p);
  $("#tour-step").textContent = `${i + 1} / ${TOUR.length} · ${TOUR[i].title}`;
  $("#tour-bar").style.width = `${(p / (TOUR.length - 1)) * 100}%`;
  $("#scroll-hint").style.opacity = p > 0.15 ? 0 : 1;
}
addEventListener("scroll", onScroll, { passive: true });
addEventListener("resize", onScroll);

// 6. Шторка «Подробнее»
function openSheet(focusRemark = false) {
  $("#sheet").hidden = false;
  $("#sheet-backdrop").hidden = false;
  $("#sheet").scrollTop = 0;
  if (focusRemark) {
    $("#remark-box").scrollIntoView({ block: "center" });
    $("#remark-text").focus();
  }
}
function closeSheet() {
  $("#sheet").hidden = true;
  $("#sheet-backdrop").hidden = true;
}
$("#btn-more").addEventListener("click", () => openSheet());
$("#sheet-close").addEventListener("click", closeSheet);
$("#sheet-backdrop").addEventListener("click", closeSheet);
addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

function chipsHtml(chips) {
  return chips.length
    ? chips.map((c) => `<button class="chip ${c.must ? "must" : ""}" data-key="${esc(c.key)}">${esc(c.label)}<span class="x">×</span></button>`).join("")
    : `<span class="alt">Пока без условий</span>`;
}

const DRIVE = { fwd: "передний", rwd: "задний", awd: "полный" };
const spec = (value, label) => value == null || value === "" ? "" :
  `<div class="spec"><b>${esc(value ?? "—")}</b>${esc(label)}</div>`;

function renderDetails(rec) {
  const c = rec.car;
  const s = c.specs || {};
  $("#car-title").textContent = c.title;
  $("#car-price").textContent = `${c.trim} · от ${money(c.price)}`;
  const block = (title, html) => html.trim() ? `<h3 class="specs-title">${title}</h3><div class="specs">${html}</div>` : "";
  $("#result-body").innerHTML = `
    <h2 class="car-title">${esc(c.title)}</h2>
    <p class="car-meta">${esc(c.trim)} · ${esc(c.year)} · от ${money(c.price)}${rec.budget_note ? ` · ${esc(rec.budget_note)}` : ""}</p>
    ${block("Динамика", `
      ${spec(c.power_hp, "л.с.")}
      ${spec(s.accel_0_100, "с до 100 км/ч")}
      ${spec(s.top_speed, "км/ч максимум")}
      ${spec(c.consumption_l100, "л/100 км")}
      ${spec(s.tank_l, "л — бак")}
      ${spec(DRIVE[c.drive], "привод")}`)}
    ${block("Размеры и безопасность", `
      ${spec(c.trunk_l, "л — багажник")}
      ${spec(s.clearance_mm, "мм — клиренс")}
      ${spec(s.weight_kg, "кг — масса")}
      ${spec(s.length_mm && `${(s.length_mm / 1000).toFixed(2)} м`, "длина")}
      ${spec(s.airbags, "подушек")}
      ${spec(s.esp === undefined ? null : s.esp ? "есть" : "нет", "ESP")}`)}
    <p class="alt">${c.market ? `Данные из объявления (${esc(c.source || "Drom")}) и каталога Drom` : "Характеристики ориентировочные (тестовый каталог)."}</p>
    <div class="block pros"><h3>Почему подходит</h3><ul>${rec.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>
    ${rec.tradeoffs.length ? `<div class="block cons"><h3>Компромиссы</h3><ul>${rec.tradeoffs.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>` : ""}
    ${rec.alternatives.length ? `<div class="block"><h3>Альтернативы</h3><ul>${rec.alternatives.map((a, i) => a.car ? `<li><button class="link alt-open" data-alt="${i}">${esc(a.title)}</button> <span class="alt">${esc(a.diff)}</span></li>` : `<li>${esc(a.title)} <span class="alt">${esc(a.diff)}</span></li>`).join("")}</ul></div>` : ""}
    ${c.listing_url ? `<a class="secondary" style="display:flex;align-items:center;justify-content:center;text-decoration:none" href="${esc(c.listing_url)}" target="_blank" rel="noopener">Смотреть объявление</a>` : ""}`;
  $("#profile-chips").innerHTML = chipsHtml(rec.chips);
  $("#model-credit").textContent = c.model_credit ? `3D: ${c.model_credit}` : gallery.items.length ? "Готовой 3D-модели этой машины пока нет — показана общая сцена; фото: Wikimedia Commons, свободные лицензии" : "";
}

function renderEmpty(rec) {
  $("#empty-body").innerHTML = `
    <h2 class="car-title">Ничего не нашлось</h2>
    <div class="block cons"><h3>Почему</h3><ul>${rec.why_empty.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>
    ${rec.uncertain.length ? `<p class="alt">Возможно подойдут (не хватает данных): ${esc(rec.uncertain.join(", "))}</p>` : ""}
    <div class="empty-cf">${rec.counterfactuals.map((c, i) => `<button class="secondary" data-cf="${i}">${esc(c.text)}</button>`).join("")}</div>`;
  $("#empty-chips").innerHTML = chipsHtml(rec.chips);
}

// правка условий: убрать чип или применить «что если»
function removeChip(e) {
  const key = e.target.closest(".chip")?.dataset.key;
  if (!key) return;
  guarded(async () => {
    await api("PATCH", `/sessions/${state.sid}/profile`, { remove: key });
    await showResult(refetchRec());
  });
}
$("#profile-chips").addEventListener("click", removeChip);
$("#empty-chips").addEventListener("click", removeChip);
$("#empty-body").addEventListener("click", (e) => {
  const i = e.target.closest("[data-cf]")?.dataset.cf;
  if (i === undefined) return;
  const a = state.rec.counterfactuals[i].action;
  const patch = a.type === "budget" ? { budget_max: a.value } : { remove: a.key };
  guarded(async () => {
    await api("PATCH", `/sessions/${state.sid}/profile`, patch);
    await showResult(refetchRec());
  });
});

// 8. Тест на лося: исход считает API, 3D-показ идёт в той же сцене экскурсии (moose3d.js).
//    2D-сцена (moose2d.js) — только запасной вариант, если 3D не загрузилось.
const moose = { cache: new Map(), busy: false, scene: null, test: null, mode: null };
const mooseSpeed = () => Number(moose.mode === "2d" ? $("#mm-speed").value : $("#mm3d-speed").value);
const formatSpeed = (v) => v.toFixed(1).replace(".", ",");
const report = $("#mm-report");
report.addEventListener("close", () => {
  if (moose.mode) document.getElementById(report.dataset.trigger)?.focus();
});
const panelObserver = new ResizeObserver(() => moose.test?.setBottomInset($("#mm-test-panel").offsetHeight));

async function openMoose() {
  const c = state.rec?.car;
  if (!c || moose.mode) return;
  moose.mode = "3d";
  $("#stage").classList.add("moose-running");
  $("#moose-overlay").hidden = false;
  document.body.classList.add("moose-lock");
  try {
    moose.test ??= new MooseTest(viewer, $("#moose-overlay"));
    panelObserver.observe($("#mm-test-panel"));
    moose.test.setBottomInset($("#mm-test-panel").offsetHeight);
    await moose.test.enter();
    if (location.search.includes("debug")) window.mooseTest = moose.test;
  } catch (e) {
    console.warn("3D-тест недоступен, показываю 2D", e);
    leave3d();
    moose.mode = "2d";
    $("#moose-modal").hidden = false;
    $("#mm-car").textContent = `${c.title} · ${c.trim}`;
    $("#mm-credit").textContent = `Лось: WildMesh 3D, CC BY 4.0 · машина: ${c.model_credit || "атрибуция указана в карточке"}`;
    moose.scene ??= new MooseScene($("#mm-canvas"));
    moose.scene.resize();
    moose.scene.setCar({ color: state.color && c.colors.includes(state.color) ? state.color : c.colors[0], body: c.body });
  }
}
function leave3d() {
  panelObserver.disconnect();
  moose.test?.exit();
  $("#moose-overlay").hidden = true;
  $("#stage").classList.remove("moose-running");
  document.body.classList.remove("moose-lock");
}
function closeMoose() {
  if (!moose.mode) return;
  if (report.open) report.close();
  if (moose.mode === "3d") leave3d();
  else { moose.scene?.stop(); $("#moose-modal").hidden = true; }
  moose.mode = null;
}
$("#btn-moose").addEventListener("click", openMoose);
$("#mm-close").addEventListener("click", closeMoose);
$("#mm3d-close").addEventListener("click", closeMoose);
addEventListener("keydown", (e) => { if (e.key === "Escape" && !report.open) closeMoose(); });
for (const id of ["mm-speed", "mm3d-speed"]) {
  $(`#${id}`).addEventListener("input", (e) => { $(`#${id}-val`).textContent = `${formatSpeed(e.target.valueAsNumber)} км/ч`; });
}

async function runMoose(kind) {
  if (moose.busy || !moose.mode) return;
  moose.busy = true;
  const controls = ".mm-actions button, #mm-speed, #mm3d-speed";
  document.querySelectorAll(controls).forEach((b) => (b.disabled = true));
  try {
    const speed = mooseSpeed();
    const key = `${state.rec.car.id}:${speed}`;
    if (!moose.cache.has(key)) moose.cache.set(key, state.rec.car.market
      ? await api("POST", "/moose", { car: state.rec.car, speed })
      : await api("GET", `/cars/${state.rec.car.id}/moose?speed=${speed}`));
    const data = moose.cache.get(key);
    const how = moose.mode === "3d" ? await moose.test.play(kind, data, speed) : await moose.scene.play(kind, data, speed);
    if (how !== "cancelled" && moose.mode) renderMoose(kind, data, speed);
  } catch (e) {
    toast(e.message);
  } finally {
    moose.busy = false;
    document.querySelectorAll(controls).forEach((b) => (b.disabled = false));
  }
}
$("#mm-swerve").addEventListener("click", () => runMoose("swerve"));
$("#mm-hit").addEventListener("click", () => runMoose("hit"));
$("#mm3d-swerve").addEventListener("click", () => runMoose("swerve"));
$("#mm3d-hit").addEventListener("click", () => runMoose("hit"));

const SEAT_NAMES = { driver: "Водитель", passenger: "Пассажир спереди", rear: "Пассажир сзади" };
const ZONES = { head: "голова и шея", chest: "грудь", legs: "ноги" };
const SEV_COLOR = { low: "#3ecf8e", medium: "#ffd23f", high: "#ff8c2a", critical: "#ff3b3b" };

function sourceChip(src) {
  const text = src.basis === "real" ? `Реальный тест: ${src.label}` : `Оценка модели: ${src.label}`;
  return src.url
    ? `<a class="mm-src ${src.basis}" href="${esc(src.url)}" target="_blank" rel="noopener">${esc(text)} ↗</a>`
    : `<span class="mm-src ${src.basis}">${esc(text)}</span>`;
}

function renderMoose(kind, data, speed) {
  const r = kind === "swerve" ? data.swerve : data.collision;
  const hit = kind === "hit";
  const load = { low: "низкая", medium: "средняя", high: "высокая", critical: "критическая" };
  const title = hit ? "Столкновения избежать не удалось" : {
    pass: "Машина объехала лося", limit: "Лося удалось объехать, но на пределе",
    fail: r.mode === "skid" ? "Машину занесло" : "Возник риск опрокидывания",
  }[r.verdict];
  const summary = hit ? (r.roof_hit ? "После удара лось попал на лобовое стекло и крышу." : "Основной удар пришёлся на бампер и капот.") : r.text;
  const seats = hit ? `<h3>Нагрузка на манекены</h3><dl>${Object.entries(r.seats).map(([id, z]) =>
    `<div><dt>${SEAT_NAMES[id]}</dt><dd class="verdict-${esc(z.overall.level)}">${load[z.overall.level]}</dd></div>`).join("")}</dl>` : "";
  const zones = hit ? Object.entries(r.seats).map(([id, z]) => `<p><b>${SEAT_NAMES[id]}:</b> ${Object.entries(ZONES).map(([key, name]) => `${name} — ${load[z[key].level]}`).join("; ")}.</p>`).join("") : "";
  report.querySelector(".mm-report-body").innerHTML = `
    <small>Итог заезда · ${hit ? "Удар" : "Объезд"}</small><h2 id="mm-report-title">${esc(title)}</h2><p>${esc(summary)}</p>
    <dl><div><dt>Скорость до манёвра</dt><dd>${formatSpeed(speed)} км/ч</dd></div>
    <div><dt>${hit ? "Скорость при ударе" : "Предел объезда для этой машины"}</dt><dd>≈ ${esc(hit ? r.impact_speed : r.v_max)} км/ч</dd></div></dl>
    ${seats}<details><summary>Откуда эти цифры?</summary>${sourceChip(r.source)}${zones}<ul>${r.factors.map(f => `<li>${esc(f)}</li>`).join("")}</ul></details>
    <small>${esc(data.disclaimer)}</small>`;
  report.dataset.trigger = `${moose.mode === "2d" ? "mm" : "mm3d"}-${kind}`;
  report.showModal();
}

// 7. Нравится / не то
$("#btn-like").addEventListener("click", () => guarded(async () => {
  const car = state.rec.car;
  if (car.market) {                               // объявление не из каталога: в кабинет не сохраняем, ссылка — в «Подробнее»
    if (state.sid) await api("POST", `/sessions/${state.sid}/feedback`, { car_id: null, liked: true }).catch(() => {});
    return toast(`Отлично! ${car.title} — ваш выбор. Ссылка на объявление — в «Подробнее»`);
  }
  if (state.sid) await api("POST", `/sessions/${state.sid}/feedback`, { car_id: car.id, liked: true });
  if (state.user) {
    await api("PUT", `/me/saved/${car.id}`);
    toast(`${car.title} сохранена в кабинете`);
  } else {
    toast(`Отлично! ${car.title} — ваш выбор. Войдите, чтобы сохранять машины`);
  }
}));
$("#btn-dislike").addEventListener("click", () => openSheet(true));
$("#remark-go").addEventListener("click", () => {
  const text = $("#remark-text").value.trim();
  $("#remark-text").value = "";
  if (!state.sid) return toast("Начните новый подбор, чтобы подобрать другую машину");
  if (state.rec.car.market) {
    const skip = [state.rec.car.model_path, ...(state.excluded || [])].filter(Boolean);
    state.excluded = skip;
    return showResult(api("POST", `/sessions/${state.sid}/feedback`, { car_id: null, liked: false, text })
      .then(() => api("GET", `/sessions/${state.sid}/market?exclude=${encodeURIComponent(skip.join(","))}`)));
  }
  showResult(api("POST", `/sessions/${state.sid}/feedback`, { car_id: state.rec.car.id, liked: false, text }));
});


// 9. Аккаунт: вход и регистрация, сохранённые, история поиска, настройки
const MODE_RU = { novice: "Новичок", expert: "Эксперт" };
const MODE_DESC = { novice: "Простые вопросы", expert: "Привод, коробка, динамика" };
const BODY_RU = { sedan: "Седан", hatch: "Хэтчбек", suv: "Кроссовер", wagon: "Универсал", minivan: "Минивэн" };
const FUEL_RU = { petrol: "Бензин", diesel: "Дизель", hybrid: "Гибрид", electric: "Электро" };
const svg = (d, cls = "") => `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const I = {
  person: svg('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
  novice: svg('<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>'),
  expert: svg('<path d="M12 14l4-4"/><path d="M3.3 17a9 9 0 1 1 17.4 0"/>'),
  arrow: svg('<path d="M5 12h14M13 6l6 6-6 6"/>'),
  heart: svg('<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z" fill="currentColor"/>'),
  car: (cls = "") => svg('<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>', cls),
  clock: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
};
const accName = (u) => u.name || u.email.split("@")[0];
const setSeg = (seg, i) => seg.style.setProperty("--i", i);

async function refreshUser() {
  const { user: u } = await api("GET", "/me").catch(() => ({ user: null }));
  state.user = u;
  document.querySelectorAll(".acc-btn").forEach((b) => {  // кнопка в шапке и в превью машины
    b.querySelector(".acc-name").textContent = u ? accName(u) : "Войти";
    b.querySelector(".avatar").innerHTML = u ? esc(accName(u)[0].toUpperCase()) : I.person;
    b.classList.toggle("authed", !!u);
  });
  $("#pick-hint").hidden = !u;
}

// --- вход и регистрация
const authDialog = $("#auth-dialog"), authForm = $("#auth-form"), authSeg = $("#auth-seg");
let authKind = "login";
const COPY = {
  login: ["С возвращением", "Войдите, чтобы сохранять машины и историю поиска", "Войти"],
  register: ["Создайте аккаунт", "Сохраняйте понравившиеся машины и возвращайтесь к прошлым поискам", "Создать аккаунт"],
};
function setAuthKind(kind) {
  authKind = kind;
  setSeg(authSeg, kind === "login" ? 0 : 1);
  authSeg.querySelectorAll("[data-auth]").forEach((b) => b.classList.toggle("active", b.dataset.auth === kind));
  authDialog.querySelectorAll(".reg-only").forEach((el) => (el.hidden = kind !== "register"));
  [$("#auth-title").textContent, $("#auth-sub").textContent, $("#auth-submit .btn-text").textContent] = COPY[kind];
  authForm.password.autocomplete = kind === "login" ? "current-password" : "new-password";
  $("#auth-error").hidden = true;
}
function openAuth(kind = "login", note = "") {
  setAuthKind(kind);
  if (note) $("#auth-sub").textContent = note;
  authDialog.showModal();
  authForm.email.focus();
}
authSeg.addEventListener("click", (e) => { const b = e.target.closest("[data-auth]"); if (b) setAuthKind(b.dataset.auth); });
$("#auth-cancel").addEventListener("click", () => authDialog.close());
authDialog.addEventListener("close", () => setTimeout(() => {
  if (state.user) return;
  state.afterAuth = null;
  if (location.pathname.startsWith("/account")) history.replaceState({}, "", "/" + location.search);
}, 0));
authDialog.addEventListener("click", (e) => { if (e.target === authDialog) authDialog.close(); });
$("#pw-eye").addEventListener("click", () => {
  const f = authForm.password;
  f.type = f.type === "password" ? "text" : "password";
  $("#pw-eye").classList.toggle("on", f.type === "text");
});
authForm.password.addEventListener("input", () => {
  const v = authForm.password.value;
  const ok = { len: v.length >= 8, letter: /[A-Za-zА-Яа-яЁё]/.test(v), digit: /\d/.test(v) };
  document.querySelectorAll("#pw-rules li").forEach((li) => li.classList.toggle("ok", ok[li.dataset.rule]));
});
authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(authForm), btn = $("#auth-submit"), err = $("#auth-error");
  const body = { email: f.get("email"), password: f.get("password") };
  if (authKind === "register") body.name = f.get("name");
  err.hidden = true;
  btn.classList.add("loading"); btn.disabled = true;
  try {
    await Promise.all([api("POST", `/auth/${authKind}`, body), sleep(450)]);
    const next = state.afterAuth;  // запоминаем до закрытия окна: оно сбрасывает afterAuth
    state.afterAuth = null;
    authForm.reset();
    authDialog.close();
    await refreshUser();
    toast(authKind === "register" ? "Аккаунт создан — добро пожаловать!" : "Вы вошли в аккаунт");
    if (next) guarded(next);
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
    err.style.animation = "none"; void err.offsetWidth; err.style.animation = "";
  } finally {
    btn.classList.remove("loading"); btn.disabled = false;
  }
});

// --- кабинет
async function showCar(car, { push = true } = {}) {
  state.rec = { car, reasons: [], tradeoffs: [], alternatives: [], chips: [] };
  state.rec.car = await prepareCar(car, car.colors?.[0] || "gray");
  renderDetails(state.rec);
  applyTrack();
  show("screen-result", { push });
  viewer.start();
  onScroll();
}

const when = (iso) => new Date(iso).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
const empty = (icon, title, text) => `<div class="empty"><div class="empty-ico">${icon}</div><h3>${title}</h3><p>${text}</p>
  <button class="btn-solid wide" data-go="search">Подобрать машину</button></div>`;
const modeOpts = (cur) => ["novice", "expert"].map((m) => `<label class="mode-opt"><input type="radio" name="set-mode" value="${m}" ${cur === m ? "checked" : ""}>
  <span class="mode-opt-card">${I[m]}<b>${MODE_RU[m]}</b><small>${MODE_DESC[m]}</small><i class="tick"></i></span></label>`).join("");

// Силуэты кузовов для баннера карточки (линия крыши зависит от типа кузова)
const ROOF = {
  sedan: "M6 32V25L24 21L40 11H72L88 21L112 24V32",
  hatch: "M6 32V25L24 21L40 11H80L106 18L112 22V32",
  suv: "M6 32V22L22 19L36 8H92L108 12L112 20V32",
  wagon: "M6 32V25L24 21L40 11H96L112 14V32",
  minivan: "M6 32V22L26 16L42 8H98L112 14V32",
};
const silhouette = (body) => `<svg class="car-sil" viewBox="0 0 120 44" aria-hidden="true"><path d="${ROOF[body] || ROOF.sedan}"/>
  <path d="M6 32H20M40 32H82M102 32H112"/><circle cx="30" cy="32" r="8"/><circle cx="92" cy="32" r="8"/></svg>`;

function carCard(car, i) {
  const s = car.specs || {};
  const specs = [car.power_hp && `${car.power_hp} л.с.`, FUEL_RU[car.fuel], car.consumption_l100 && `${car.consumption_l100} л/100 км`,
    car.transmission && (car.transmission === "auto" ? "Автомат" : "Механика")].filter(Boolean);
  return `<article class="car-card" style="--i:${i}">
    <div class="car-top"><span class="pill">${BODY_RU[car.body] || car.body}</span>${silhouette(car.body)}</div>
    <div class="car-body"><h3>${esc(car.title)}</h3><p>${esc(car.trim)}${car.year ? ` · ${car.year}` : ""}</p>
      <div class="specs">${specs.map((t) => `<span>${esc(t)}</span>`).join("")}</div>
      <div class="car-price">${money(car.price)}</div></div>
    <div class="car-foot"><button class="btn-solid" data-open="${car.id}">Смотреть в 3D</button>
      <button class="icon-btn" data-unsave="${car.id}" aria-label="Убрать из сохранённых" title="Убрать из сохранённых">${I.heart}</button></div></article>`;
}

let accTab = "saved", accReq = 0;
const accData = { cars: {}, hist: [] };
async function openAccount(tab = accTab, { push = true } = {}) {
  const u = state.user, req = ++accReq;
  accTab = tab;
  show("screen-account", { push });
  $("#acc-hero-avatar").textContent = accName(u)[0].toUpperCase();
  $("#acc-title").textContent = accName(u);
  $("#acc-mail").textContent = u.email;
  $("#acc-mode").innerHTML = u.mode ? `${I[u.mode]}${MODE_RU[u.mode]}` : `${I.person}Режим не выбран`;
  const tabs = ["saved", "history", "settings"];
  setSeg($("#acc-tabs"), tabs.indexOf(tab));
  document.querySelectorAll("#acc-tabs .seg-btn").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  const box = $("#acc-body");
  box.className = `acc-body${tab === "saved" ? " cards-grid" : ""}`;
  box.innerHTML = tab === "settings" ? "" : `<div class="skel"></div>`.repeat(tab === "saved" ? 3 : 2);
  if (tab === "saved") {
    const { items } = await api("GET", "/me/saved");
    if (req !== accReq) return;
    accData.cars = Object.fromEntries(items.map(({ car }) => [car.id, car]));
    box.innerHTML = items.length ? items.map(({ car }, i) => carCard(car, i)).join("")
      : empty(I.heart, "Пока ничего не сохранено", "Нажмите «Нравится» на подходящей машине — она появится здесь, и к ней можно вернуться в любой момент.");
    $("#cnt-saved").hidden = !items.length;
    $("#cnt-saved").textContent = items.length;
  } else if (tab === "history") {
    const { items } = await api("GET", "/me/history");
    if (req !== accReq) return;
    accData.hist = items;
    box.innerHTML = items.length ? items.map((h, i) => `<article class="hist" style="--i:${i}">
      <div class="hist-rail"><i></i></div>
      <div class="hist-main"><time>${when(h.created_at)}</time>
        <h3>${h.query ? `«${esc(h.query)}»` : "Подбор по вопросам"}</h3>
        <div class="chips">${h.chips.map((c) => `<span class="chip">${esc(c)}</span>`).join("")}</div>
        ${h.car ? `<div class="hist-car">${I.car()}<span><b>${esc(h.car.title)}</b> · ${esc(h.car.trim)} · ${money(h.car.price)}</span></div>` : ""}</div>
      <button class="btn-ghost" data-session="${i}">Открыть${I.arrow}</button></article>`).join("")
      : empty(I.clock, "История пока пуста", "Здесь появятся ваши поиски: можно открыть любой из них и продолжить с того же места.");
  } else {
    box.innerHTML = `
      <section class="set-card" style="--i:0"><h3>Профиль</h3><p>Как к вам обращаться в интерфейсе.</p>
        <div class="set-row"><input class="set-input" id="set-name" maxlength="40" placeholder="Ваше имя" value="${esc(u.name || "")}"></div></section>
      <section class="set-card" style="--i:1"><h3>Как вы разбираетесь в машинах</h3><p>От этого зависят вопросы при подборе. Меняйте в любой момент.</p>
        <div class="mode-opts">${modeOpts(u.mode)}</div></section>
      <div class="set-actions" style="--i:2"><button class="btn-solid wide" id="set-save">Сохранить</button>
        <button class="btn-ghost btn-out" id="set-logout"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>Выйти из аккаунта</button></div>`;
  }
}

$("#acc-body").addEventListener("click", (e) => guarded(async () => {
  const t = e.target.closest("button");
  if (!t) return;
  if (t.dataset.open) await showCar(accData.cars[t.dataset.open]);
  else if (t.dataset.unsave) {
    t.closest(".car-card").classList.add("leaving");
    await Promise.all([api("DELETE", `/me/saved/${t.dataset.unsave}`), sleep(280)]);
    await openAccount("saved");
  } else if (t.dataset.session) {
    state.sid = accData.hist[t.dataset.session].session_id;
    resetSearch();
    state.color = null;
    await showResult(api("GET", `/sessions/${state.sid}/recommendation`));
  } else if (t.dataset.go) show("screen-mode");
  else if (t.id === "set-save") {
    const mode = document.querySelector("[name=set-mode]:checked")?.value;
    await api("PATCH", "/me", { name: $("#set-name").value, ...(mode ? { mode } : {}) });
    await refreshUser();
    await openAccount("settings");
    toast("Настройки сохранены");
  } else if (t.id === "set-logout") await logout();
}));
async function logout() {
  await api("POST", "/auth/logout");
  state.sid = null;
  await refreshUser();
  show("screen-mode");
  toast("Вы вышли из аккаунта");
}
$("#hero-logout").addEventListener("click", () => guarded(logout));
$("#acc-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (b) guarded(() => openAccount(b.dataset.tab)); });
$("#acc-back").addEventListener("click", () => show("screen-mode"));
document.querySelectorAll(".acc-btn").forEach((b) => b.addEventListener("click", () => (state.user ? guarded(() => openAccount("saved")) : openAuth("login"))));
// Режим спрашиваем только при первом поиске: у кого он уже сохранён в аккаунте, сразу идёт к описанию
async function startSearch() {
  if (!state.user) {  // подбор доступен только с аккаунтом: после входа продолжаем с того же места
    state.afterAuth = startSearch;
    return openAuth("register", "Чтобы подобрать машину, войдите или создайте аккаунт");
  }
  if (!state.user.mode) return show("screen-pick");
  const s = await api("POST", "/sessions", {});
  state.sid = s.session_id;
  resetSearch();
  show("screen-describe");
  $("#describe-text").focus();
}
$("#start-btn").addEventListener("click", () => guarded(startSearch));
$("#pick-back").addEventListener("click", () => show("screen-mode"));

// маршрутизатор: выбирает экран по адресу (при открытии ссылки и при «назад/вперёд»)
async function route(path) {
  const [, a, b] = path.split("/");
  const home = () => {
    show("screen-mode", { push: false });
    if (location.pathname !== "/") history.replaceState({}, "", "/" + location.search);
  };
  try {
    if (a === "account") {
      if (!state.user) {  // кабинет только для вошедших: просим войти и после входа открываем тот же адрес
        show("screen-mode", { push: false });
        state.afterAuth = () => route(path);
        return openAuth("login", "Войдите, чтобы открыть кабинет");
      }
      return await openAccount(["saved", "history", "settings"].includes(b) ? b : "saved", { push: false });
    }
    if (a === "car" && b) return await showCar(await api("GET", `/cars/${b}`), { push: false });
    if (a === "start") return show("screen-pick", { push: false });
    if (a === "describe" && state.sid) return show("screen-describe", { push: false });
    if (a === "question" && state.sid) {
      if (form.sid === state.sid && form.questions.size) {                // «назад» из результата: анкета на месте, нейросеть не дёргаем
        show("screen-question", { push: false });
        qaLock(false);
        return;
      }
      return await nextQuestion();
    }
    if (a === "result" && state.sid) {
      const rec = state.rec ?? loadRec();
      if (rec?.car) return await showResult(Promise.resolve(rec), { instant: true });
    }
    if (a === "empty" && state.sid) return await showResult(api("GET", `/sessions/${state.sid}/recommendation`));
  } catch (e) { /* поиск не найден, неизвестная машина и т.п. */ }
  home();
}
addEventListener("popstate", () => route(location.pathname));
refreshUser().then(() => route(location.pathname));

// альтернативы из объявлений открываются той же сценой: 3D, подписи и тест на лося по данным выбранного объявления
$("#result-body").addEventListener("click", (e) => {
  const b = e.target.closest("[data-alt]");
  if (!b || !state.rec?.alternatives) return;
  const alt = state.rec.alternatives[+b.dataset.alt];
  const rest = state.rec.alternatives.filter((_, i) => i !== +b.dataset.alt).concat([{ id: state.rec.car.id, title: `${state.rec.car.title} ${state.rec.car.year || ""}`.trim(), diff: `${(state.rec.car.price || 0).toLocaleString("ru-RU")} ₽`, car: state.rec.car }]);
  closeSheet();
  guarded(async () => {
    show("screen-loading");
    const car = await prepareCar(alt.car, alt.car.colors?.[0] || "gray");
    state.rec = { ...state.rec, car, alternatives: rest, reasons: [`${car.title}: ${(car.price || 0).toLocaleString("ru-RU")} ₽, ${car.year || ""} г.`], tradeoffs: [] };
    renderDetails(state.rec);
    applyTrack();
    show("screen-result");
    viewer.start();
    onScroll();
  });
});
