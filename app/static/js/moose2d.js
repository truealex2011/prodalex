// «Тест на лося» в 2D (Canvas). Исход считает бэкенд (/cars/{id}/moose) — здесь только показ.
//  • Объезд: вид сверху. Лось резко выпрыгивает из кустов, машина уходит во встречную полосу и
//    возвращается; на пределе — раскачка, при провале — занос или постановка на два колеса.
//  • Удар: вид сбоку в разрезе, внутри манекены (водитель, пассажир, сзади). Замедленная съёмка,
//    подушки, деформация; после удара зоны манекенов окрашиваются по тяжести травм.

const PAINT = {
  red: "#c0262d", white: "#eef0f2", black: "#26292e", blue: "#2456b8", gray: "#8a9099",
  green: "#2f6d3a", silver: "#c3c8ce", yellow: "#e8b923", orange: "#e27416", brown: "#6d4c41",
};
const SEVERITY = { low: "#3ecf8e", medium: "#ffd23f", high: "#ff8c2a", critical: "#ff3b3b" };
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const lerp = (a, b, t) => a + (b - a) * t;
const ease = (t) => 1 - Math.pow(1 - clamp(t, 0, 1), 3);
const smooth = (t) => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
const rnd = (i) => { const x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
const LANE = 3.5;

function shade(hex, k) { // k<0 темнее, k>0 светлее
  const n = parseInt(hex.slice(1), 16);
  const f = (c) => Math.round(clamp(k < 0 ? c * (1 + k) : c + (255 - c) * k, 0, 255));
  return `rgb(${f(n >> 16)},${f((n >> 8) & 255)},${f(n & 255)})`;
}
function rr(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

export class MooseScene {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.car = { color: PAINT.white, body: "sedan" };
    this.sim = null;
    this.raf = null;
    new ResizeObserver(() => { this.resize(); this.draw(); }).observe(canvas);
    this.resize();
  }

  setCar({ color, body }) {
    this.car = { color: PAINT[color] || PAINT.gray, body: body || "sedan" };
    this.sim = null;
    this.draw();
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2);
    this.W = Math.max(1, r.width); this.H = Math.max(1, r.height);
    this.canvas.width = Math.round(this.W * dpr); this.canvas.height = Math.round(this.H * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  /** Запуск сценария. kind: "swerve" | "hit". Возвращает Promise — по окончании анимации. */
  play(kind, result, speedKmh) {
    this.stop();
    return new Promise((resolve) => {
      this.sim = kind === "swerve" ? this.initSwerve(result.swerve, speedKmh) : this.initHit(result.collision, speedKmh);
      this.sim.resolve = resolve;
      let last = performance.now();
      const loop = (now) => {
        const dt = Math.min(0.05, (now - last) / 1000); last = now;
        this.step(dt);
        this.draw();
        if (this.sim && !this.sim.done) this.raf = requestAnimationFrame(loop);
      };
      this.raf = requestAnimationFrame(loop);
    });
  }

  stop() {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = null;
    if (this.sim && !this.sim.done) { this.sim.done = true; this.sim.resolve?.(); }
  }

  /** Шаг симуляции (dt — реальные секунды; внутри может идти замедленная съёмка). */
  step(dt) {
    const S = this.sim;
    if (!S || S.done) return;
    const sdt = dt * (S.slow ? 0.3 : 1);
    S.t += sdt; S.real += dt;
    if (S.kind === "swerve") this.stepSwerve(S, sdt); else this.stepHit(S, sdt);
    if (S.endAt != null && S.real >= S.endAt) { S.done = true; S.resolve?.(); }
  }

  draw() {
    const { ctx, W, H } = this;
    ctx.clearRect(0, 0, W, H);
    if (!this.sim) return this.drawIdle();
    if (this.sim.kind === "swerve") this.drawSwerve(this.sim); else this.drawHit(this.sim);
  }

  drawIdle() {
    const { ctx, W, H } = this;
    this.drawSwerve(this.initSwerve({ verdict: "pass", mode: "skid" }, 60), true);
    ctx.fillStyle = "rgba(10,12,16,.45)"; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = "#fff"; ctx.font = "600 16px system-ui, sans-serif"; ctx.textAlign = "center";
    ctx.fillText("Выберите скорость и сценарий", W / 2, H / 2);
  }

  // ======================================================================================
  //  ОБЪЕЗД — вид сверху
  // ======================================================================================
  initSwerve(r, speedKmh) {
    const v = speedKmh / 3.6;
    const L = 4.4;
    return {
      kind: "swerve", r, v0: v, v, t: 0, real: 0, x: 0, y: 0, psi: 0, roll: 0, latG: 0, L,
      mooseX: 34 + v * 0.6, mooseY: 6.6, mooseH: 0, mooseState: "hidden", legT: 0,
      phase: "drive", skid: [[], [], [], []], caption: "", captionT: 0, braking: false, endAt: null,
    };
  }

  swervePath(u, A) {
    if (u <= 0) return 0;
    if (u < 11) return -A * (1 - Math.cos(Math.PI * u / 11)) / 2;
    if (u < 15) return -A;
    if (u < 28) return -A * (1 + Math.cos(Math.PI * (u - 15) / 13)) / 2;
    return 0;
  }

  say(S, text) { S.caption = text; S.captionT = S.real; }

  stepSwerve(S, dt) {
    const front = S.x + S.L / 2;
    const gap = S.mooseX - front;
    // --- лось: сидит в кустах, выпрыгивает РЕЗКО, когда до него ~1,2 с езды ---
    if (S.mooseState === "hidden" && gap <= 13 + S.v0 * 0.6) {
      S.mooseState = "leap"; S.leapT = 0; S.slow = true; this.say(S, "ЛОСЬ!");
    }
    if (S.mooseState === "leap") {
      S.leapT += dt; S.legT += dt;
      const k = clamp(S.leapT / 0.32, 0, 1);              // прыжок 0,32 с — из кустов прямо в полосу
      S.mooseY = lerp(6.6, 0.4, ease(k));
      S.mooseH = Math.sin(k * Math.PI) * 1.1;
      if (k >= 1) { S.mooseState = "stand"; S.mooseH = 0; }
    }
    if (S.mooseState === "stand" && S.x - S.mooseX > 4) { S.mooseState = "run"; }
    if (S.mooseState === "run") { S.mooseY -= 6 * dt; S.legT += dt; }

    const { verdict, mode } = S.r;
    const A = LANE;
    if (S.phase === "drive") {
      S.x += S.v * dt;
      if (S.uStart == null && S.mooseState !== "hidden" && gap <= 13) { S.uStart = S.x; this.say(S, "Руль влево!"); }
      const u = S.uStart == null ? -1 : S.x - S.uStart;
      const wob = (q) => (verdict === "limit" && q > 15 ? 0.7 * Math.sin((q - 15) * 0.5) * Math.exp(-(q - 15) / 10) : 0);
      const lat = (q) => this.swervePath(q, A) + wob(q);
      const y0 = S.y;
      S.y = lat(u);
      const d1 = (lat(u + 0.25) - lat(u - 0.25)) / 0.5;
      const d2 = (lat(u + 0.5) - 2 * lat(u) + lat(u - 0.5)) / 0.25;
      S.psi = Math.atan(-d1);                              // положительный курс — влево (вверх на экране)
      S.latG = (-d2 * S.v * S.v) / 9.81;
      const gain = verdict === "pass" ? 0.07 : verdict === "limit" ? 0.14 : 0.16;
      S.roll = clamp(S.latG * gain, -0.3, 0.3);
      if (u > 15 && u < 16 && S.caption !== "Обратно в полосу") this.say(S, "Обратно в полосу");
      if (verdict === "fail" && mode === "skid" && u > 17) {
        S.phase = "skid"; S.dir = S.psi; S.spin = -2.1; this.say(S, "Занос!");
      }
      if (verdict === "fail" && mode === "rollover" && u > 14) {
        S.phase = "tip"; S.dir = S.psi; S.tipT = 0; this.say(S, "Встаёт на два колеса!");
      }
      if (u > 22) S.slow = false;
      if (u > 30) { S.phase = "stop"; S.braking = true; this.say(S, verdict === "limit" ? "Удержал — на пределе" : "Объехал!"); }
      if (Math.abs(S.latG) > 0.45 || (verdict === "limit" && u > 15 && u < 30)) this.addSkid(S);
      void y0;
    } else if (S.phase === "stop") {
      S.v = Math.max(0, S.v - 6.5 * dt); S.x += S.v * dt;
      S.roll *= Math.exp(-dt * 6); S.psi *= Math.exp(-dt * 5); S.latG = 0;
      if (S.v < 0.05 && S.endAt == null) S.endAt = S.real + 1.0;
    } else if (S.phase === "skid") {
      S.v = Math.max(0, S.v - 6 * dt);
      S.x += S.v * Math.cos(S.dir) * dt; S.y -= S.v * Math.sin(S.dir) * dt;
      S.psi += S.spin * dt; S.spin *= Math.exp(-dt * 1.0);
      S.roll *= Math.exp(-dt * 2);
      this.addSkid(S);
      if (S.v < 0.05 && S.endAt == null) { S.endAt = S.real + 1.2; S.slow = false; }
    } else if (S.phase === "tip") {
      S.v = Math.max(0, S.v - 6.5 * dt);
      S.x += S.v * Math.cos(S.dir) * dt; S.y -= S.v * Math.sin(S.dir) * dt;
      S.tipT += dt;
      const up = smooth(S.tipT / 0.5), down = smooth((S.tipT - 1.1) / 0.45);
      S.roll = -0.62 * up * (1 - down) + (S.tipT > 1.55 && S.tipT < 1.9 ? 0.05 * Math.sin((S.tipT - 1.55) * 18) : 0);
      this.addSkid(S, true);
      if (S.tipT > 2.2 && S.endAt == null) { S.endAt = S.real + 0.8; S.slow = false; }
    }
  }

  addSkid(S, onlyRight = false) {
    const c = Math.cos(-S.psi), s = Math.sin(-S.psi);
    const pts = [[1.35, -0.78], [1.35, 0.78], [-1.35, -0.78], [-1.35, 0.78]];
    pts.forEach(([ax, ay], i) => {
      if (onlyRight && ay < 0) return; // при подъёме на два колеса следы оставляют только правые
      const wx = S.x + ax * c - ay * s, wy = S.y + ax * s + ay * c;
      const arr = S.skid[i], prev = arr[arr.length - 1];
      if (!prev || Math.hypot(prev[0] - wx, prev[1] - wy) > 0.25) arr.push([wx, wy]);
    });
  }

  drawSwerve(S, idle = false) {
    const { ctx, W, H } = this;
    // машина ближе к левому краю: впереди видно ~25 м — лось выпрыгивает в кадре
    const scale = Math.min(W / 30, H / 13);
    const camX = S.x;
    const sx = (x) => W * 0.15 + (x - camX) * scale;
    const sy = (y) => H * 0.52 + (y + 1.75) * scale;  // центр кадра — двойная сплошная

    // трава
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "#36502c"); g.addColorStop(1, "#2b4425");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    // обочины и асфальт
    ctx.fillStyle = "#6d6a62"; ctx.fillRect(0, sy(-LANE * 2 - 0.8), W, (LANE * 2 + 1.6) * scale);
    const a = ctx.createLinearGradient(0, sy(-LANE * 2), 0, sy(LANE));
    a.addColorStop(0, "#3d4046"); a.addColorStop(0.5, "#44474d"); a.addColorStop(1, "#3a3d43");
    ctx.fillStyle = a; ctx.fillRect(0, sy(-LANE - 1.75), W, LANE * 2 * scale);
    // текстура асфальта (детерминированный «шум»)
    ctx.fillStyle = "rgba(255,255,255,.035)";
    const startI = Math.floor((camX - 20) / 0.9);
    for (let i = startI; i < startI + 60; i++) {
      for (let j = 0; j < 7; j++) {
        const x = i * 0.9 + rnd(i * 7 + j) * 0.9, y = -LANE - 1.75 + rnd(i * 13 + j * 3) * LANE * 2;
        ctx.fillRect(sx(x), sy(y), 2, 2);
      }
    }
    // разметка
    ctx.fillStyle = "#e9e9e4";
    for (const y of [-LANE - 1.75, LANE - 1.75]) ctx.fillRect(0, sy(y) - 0.06 * scale, W, 0.12 * scale); // края
    ctx.fillStyle = "#e3b51c";
    ctx.fillRect(0, sy(-1.75) - 0.2 * scale, W, 0.1 * scale);
    ctx.fillRect(0, sy(-1.75) + 0.1 * scale, W, 0.1 * scale);
    // следы шин
    ctx.strokeStyle = "rgba(15,15,18,.55)"; ctx.lineWidth = 0.2 * scale; ctx.lineCap = "round";
    for (const tr of S.skid) {
      if (tr.length < 2) continue;
      ctx.beginPath(); tr.forEach(([x, y], i) => (i ? ctx.lineTo(sx(x), sy(y)) : ctx.moveTo(sx(x), sy(y)))); ctx.stroke();
    }
    // кусты и деревья по обочинам (за ними прячется лось)
    const vegetation = (side) => {
      const base = side > 0 ? LANE - 1.75 + 2.4 : -LANE - 1.75 - 2.4;
      for (let i = Math.floor((camX - 22) / 3.2); i < (camX + 22) / 3.2; i++) {
        const x = i * 3.2 + rnd(i * 3 + side) * 1.8, y = base + side * rnd(i * 5 + side) * 2.6;
        const r = 0.9 + rnd(i * 11 + side) * 0.9;
        this.bush(sx(x), sy(y), r * scale);
      }
    };
    vegetation(-1);
    // лось под кустом ещё не виден — куст рисуем поверх
    const mooseBushX = S.mooseX, mooseBushY = 6.6;
    const drawMoose = () => this.mooseTop(sx(S.mooseX), sy(S.mooseY), scale, S.mooseH, S.legT, S.mooseState);
    if (S.mooseState !== "hidden") drawMoose();
    vegetation(1);
    this.bush(sx(mooseBushX), sy(mooseBushY), 1.7 * scale, true);
    if (S.mooseState === "leap" && S.mooseY > 5) drawMoose();

    // машина
    this.carTop(sx(S.x), sy(S.y), scale, S.psi, S.roll, S.braking || S.phase === "skid");

    if (idle) return;
    this.hudSpeed(S.v * 3.6);
    this.hudRoll(S.roll);
    this.hudCaption(S);
    if (S.slow) this.badge("Замедленно ×3");
  }

  bush(x, y, r, big = false) {
    const { ctx } = this;
    ctx.save();
    ctx.fillStyle = "rgba(0,0,0,.25)";
    ctx.beginPath(); ctx.ellipse(x + r * 0.25, y + r * 0.25, r, r * 0.85, 0, 0, Math.PI * 2); ctx.fill();
    const blobs = big ? 6 : 4;
    for (let i = 0; i < blobs; i++) {
      const ang = (i / blobs) * Math.PI * 2, d = r * 0.42;
      const g = ctx.createRadialGradient(x + Math.cos(ang) * d - r * 0.2, y + Math.sin(ang) * d - r * 0.25, r * 0.1,
        x + Math.cos(ang) * d, y + Math.sin(ang) * d, r * 0.7);
      g.addColorStop(0, "#5f9a45"); g.addColorStop(1, "#2f5a25");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(x + Math.cos(ang) * d, y + Math.sin(ang) * d, r * 0.62, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();
  }

  carTop(x, y, s, psi, roll, braking) {
    const { ctx } = this;
    const L = 4.4 * s, Wd = 1.82 * s;
    const lift = Math.abs(roll) > 0.25 ? Math.abs(roll) : 0; // встаёт на два колеса
    ctx.save();
    ctx.translate(x, y); ctx.rotate(-psi);
    // тень (смещается при крене)
    ctx.fillStyle = "rgba(0,0,0,.35)";
    ctx.filter = "blur(4px)";
    rr(ctx, -L / 2 + 3, -Wd / 2 + 4 + roll * 18, L, Wd, Wd * 0.3); ctx.fill();
    ctx.filter = "none";
    // колёса
    ctx.fillStyle = "#121315";
    for (const [ax, ay] of [[0.29, -0.5], [0.29, 0.5], [-0.3, -0.5], [-0.3, 0.5]]) {
      ctx.globalAlpha = lift && ay < 0 ? 0.25 : 1;
      rr(ctx, ax * L - 0.34 * s, ay * Wd - 0.12 * s + (ay < 0 ? -0.03 * s : 0.03 * s), 0.68 * s, 0.24 * s, 0.06 * s); ctx.fill();
    }
    ctx.globalAlpha = 1;
    // кузов: при крене ширина «сжимается», кузов уходит в сторону
    ctx.translate(0, roll * 6);
    ctx.scale(1, 1 - Math.abs(roll) * 0.12);
    const body = ctx.createLinearGradient(0, -Wd / 2, 0, Wd / 2);
    body.addColorStop(0, shade(this.car.color, -0.25)); body.addColorStop(0.5, shade(this.car.color, 0.15));
    body.addColorStop(1, shade(this.car.color, -0.3));
    ctx.fillStyle = body;
    rr(ctx, -L / 2, -Wd / 2, L, Wd, Wd * 0.32); ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,.35)"; ctx.lineWidth = 1; ctx.stroke();
    // стёкла и крыша
    const glass = ctx.createLinearGradient(L * 0.1, 0, L * 0.3, 0);
    glass.addColorStop(0, "#1b2836"); glass.addColorStop(1, "#3b5570");
    ctx.fillStyle = glass;
    ctx.beginPath(); // лобовое
    ctx.moveTo(L * 0.12, -Wd * 0.4); ctx.lineTo(L * 0.27, -Wd * 0.34); ctx.lineTo(L * 0.27, Wd * 0.34); ctx.lineTo(L * 0.12, Wd * 0.4);
    ctx.closePath(); ctx.fill();
    ctx.beginPath(); // заднее
    ctx.moveTo(-L * 0.28, -Wd * 0.36); ctx.lineTo(-L * 0.36, -Wd * 0.33); ctx.lineTo(-L * 0.36, Wd * 0.33); ctx.lineTo(-L * 0.28, Wd * 0.36);
    ctx.closePath(); ctx.fill();
    ctx.fillStyle = shade(this.car.color, 0.05);
    rr(ctx, -L * 0.28, -Wd * 0.36, L * 0.4, Wd * 0.72, Wd * 0.12); ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,.12)";
    rr(ctx, -L * 0.24, -Wd * 0.3, L * 0.32, Wd * 0.14, Wd * 0.06); ctx.fill();
    // зеркала, фары, фонари
    ctx.fillStyle = shade(this.car.color, -0.35);
    rr(ctx, L * 0.1, -Wd * 0.62, 0.22 * s, 0.16 * s, 0.05 * s); ctx.fill();
    rr(ctx, L * 0.1, Wd * 0.62 - 0.16 * s, 0.22 * s, 0.16 * s, 0.05 * s); ctx.fill();
    ctx.fillStyle = "#fff6c8";
    rr(ctx, L / 2 - 0.12 * s, -Wd * 0.42, 0.1 * s, Wd * 0.2, 0.04 * s); ctx.fill();
    rr(ctx, L / 2 - 0.12 * s, Wd * 0.22, 0.1 * s, Wd * 0.2, 0.04 * s); ctx.fill();
    ctx.fillStyle = braking ? "#ff2020" : "#8a1010";
    if (braking) { ctx.shadowColor = "#ff2020"; ctx.shadowBlur = 14; }
    rr(ctx, -L / 2 + 0.02 * s, -Wd * 0.42, 0.1 * s, Wd * 0.22, 0.04 * s); ctx.fill();
    rr(ctx, -L / 2 + 0.02 * s, Wd * 0.2, 0.1 * s, Wd * 0.22, 0.04 * s); ctx.fill();
    ctx.restore();
  }

  mooseTop(x, y, s, h, legT, state) {
    const { ctx } = this;
    const k = 1 + h * 0.28; // в прыжке «ближе к камере»
    ctx.save();
    // тень на земле — отстаёт и сжимается, пока лось в воздухе
    ctx.fillStyle = "rgba(0,0,0,.35)"; ctx.filter = "blur(3px)";
    ctx.beginPath(); ctx.ellipse(x + h * 0.6 * s, y + h * 0.9 * s, 1.25 * s / k, 0.5 * s / k, 0, 0, Math.PI * 2); ctx.fill();
    ctx.filter = "none";
    ctx.translate(x, y); ctx.scale(k, k);
    ctx.rotate(state === "run" || state === "leap" ? -Math.PI / 2 : -Math.PI / 2 + 0.25); // морда — поперёк дороги (вверх)
    // ноги
    const swing = state === "leap" ? 0.55 : state === "run" ? Math.sin(legT * 16) * 0.35 : 0;
    ctx.strokeStyle = "#2a1a10"; ctx.lineWidth = 0.14 * s; ctx.lineCap = "round";
    for (const [lx, ly, ph] of [[0.75, -0.32, 1], [0.75, 0.32, -1], [-0.75, -0.32, -1], [-0.75, 0.32, 1]]) {
      ctx.beginPath(); ctx.moveTo(lx * s, ly * s);
      ctx.lineTo((lx + (lx > 0 ? 1 : -1) * (0.25 + swing * ph * 0.4)) * s, ly * 1.35 * s); ctx.stroke();
    }
    // тело
    const g = ctx.createRadialGradient(-0.2 * s, -0.15 * s, 0.1 * s, 0, 0, 1.3 * s);
    g.addColorStop(0, "#7a5233"); g.addColorStop(1, "#3e2615");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.ellipse(0, 0, 1.25 * s, 0.48 * s, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#4a2f1c";
    ctx.beginPath(); ctx.ellipse(0.55 * s, 0, 0.45 * s, 0.42 * s, 0, 0, Math.PI * 2); ctx.fill(); // холка
    // шея и голова
    ctx.beginPath(); ctx.ellipse(1.2 * s, 0, 0.35 * s, 0.2 * s, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#2f1d10";
    ctx.beginPath(); ctx.ellipse(1.65 * s, 0, 0.38 * s, 0.17 * s, 0, 0, Math.PI * 2); ctx.fill();
    // рога-лопаты
    ctx.fillStyle = "#cfb88c";
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(1.45 * s, side * 0.12 * s);
      ctx.quadraticCurveTo(1.2 * s, side * 0.75 * s, 1.55 * s, side * 0.85 * s);
      ctx.quadraticCurveTo(1.75 * s, side * 0.55 * s, 1.55 * s, side * 0.15 * s);
      ctx.fill();
    }
    ctx.restore();
  }

  // ======================================================================================
  //  УДАР — вид сбоку в разрезе, манекены
  // ======================================================================================
  initHit(r, speedKmh) {
    const v = speedKmh / 3.6;
    const L = { sedan: 4.5, hatch: 4.2, wagon: 4.55, suv: 4.6, minivan: 4.8 }[this.car.body] || 4.5;
    return {
      kind: "hit", r, v0: v, v, vImp: (r.impact_speed || 0) / 3.6, t: 0, real: 0, x: 0, L,
      mooseX: 26 + v * 0.9, mooseLeap: 0, mooseState: "hidden", phase: "drive", pitch: 0, crush: 0, roofDent: 0,
      dummy: { lean: 0, head: 0, bag: 0, rear: 0 }, caption: "", captionT: 0, endAt: null, shake: 0,
    };
  }

  stepHit(S, dt) {
    const front = S.x + S.L;
    // лось выпрыгивает резко — в ~1,5 с до машины
    if (S.mooseState === "hidden" && S.mooseX - front <= S.v0 * 1.45) {
      S.mooseState = "leap"; S.leapT = 0; this.say(S, "ЛОСЬ!");
      S.cam = null; // жёсткая склейка: камера мгновенно показывает выпрыгнувшего лося
    }
    if (S.mooseState === "leap") {
      S.leapT += dt;
      S.mooseLeap = clamp(S.leapT / 0.35, 0, 1);
      if (S.mooseLeap >= 1) S.mooseState = "stand";
    }
    if (S.phase === "drive") {
      S.x += S.v * dt;
      const brakeDist = (S.v0 * S.v0 - S.vImp * S.vImp) / (2 * 7.85);
      if (S.mooseState !== "hidden" && S.mooseX - 0.4 - front <= brakeDist) { S.phase = "brake"; this.say(S, "Тормоз!"); }
      if (S.mooseX - 0.4 - front < 4) S.slow = true;
    } else if (S.phase === "brake") {
      S.v = Math.max(S.vImp, S.v - 7.85 * dt); S.x += S.v * dt;
      S.pitch = Math.min(0.03, S.pitch + dt * 0.2);
      S.dummy.lean = Math.min(0.12, S.dummy.lean + dt * 0.6);
      if (S.mooseX - 0.4 - (S.x + S.L) <= 4) S.slow = true;
      if (S.x + S.L >= S.mooseX - 0.4) {
        S.phase = "impact"; S.impactT = S.t; S.vAt = S.v; S.shake = 1;
        this.say(S, `Удар на ${Math.round(S.v * 3.6)} км/ч`);
      }
    } else if (S.phase === "impact") {
      const k = S.t - S.impactT;                        // секунды «внутри» замедленной съёмки
      S.v = Math.max(0, S.vAt * (1 - k / 0.5)); S.x += S.v * dt;
      S.pitch = 0.03 + 0.07 * Math.sin(clamp(k / 0.45, 0, 1) * Math.PI);
      const sev = { low: 0.35, medium: 0.6, high: 0.85, critical: 1 }[S.r.level] || 0.6;
      S.crush = (S.r.roof_hit ? 0.25 : 0.45) * sev * ease(k / 0.12);
      S.roofDent = S.r.roof_hit ? 0.32 * sev * ease((k - 0.08) / 0.15) : 0;
      // манекены: рывок вперёд, подушка раскрывается за ~30 мс, отскок
      S.dummy.bag = ease(k / 0.035);
      const fwd = Math.sin(clamp(k / 0.22, 0, 1) * Math.PI);
      S.dummy.lean = 0.12 + 0.45 * fwd;
      S.dummy.head = 0.35 * Math.sin(clamp((k - 0.03) / 0.22, 0, 1) * Math.PI);
      S.dummy.rear = 0.6 * Math.sin(clamp(k / 0.26, 0, 1) * Math.PI);
      if (k > 0.55) { S.slow = false; }
      if (k > 0.75) { S.phase = "after"; this.say(S, "Что стало с людьми"); S.endAt = S.real + 1.4; }
      S.shake *= Math.exp(-dt * 6);
    } else if (S.phase === "after") {
      S.dummy.bag = Math.max(0.6, S.dummy.bag - dt * 0.5);
    }

    // камера: держит в кадре машину и лося; к удару — крупный план салона с манекенами
    const close = S.phase === "impact" || S.phase === "after" || (S.mooseX - (S.x + S.L)) < 3;
    const left = S.x - 0.8;
    const right = close || S.mooseState === "hidden" ? S.x + S.L + 2.2 : Math.max(S.mooseX + 2.2, S.x + S.L + 1);
    const minSpan = this.W < 500 ? 6.8 : 9.5;
    const span = Math.max(right - left, minSpan), cx = (left + right) / 2;
    if (!S.cam) S.cam = { span, cx };
    const k = 1 - Math.exp(-dt * (S.slow ? 14 : 4));
    S.cam.span += (span - S.cam.span) * k;
    S.cam.cx += (cx - S.cam.cx) * k;
  }

  carProfile(body, L) { // силуэт сбоку, метры (x от заднего бампера, y от земли)
    const P = {
      sedan: [[0.05, 0.32], [0, 0.72], [0.18, 0.98], [0.95, 1.02], [1.5, 1.42], [2.85, 1.45], [3.45, 1.0], [4.35, 0.9], [4.5, 0.62], [4.45, 0.32]],
      hatch: [[0.05, 0.32], [0, 0.9], [0.22, 1.4], [2.55, 1.45], [3.15, 1.0], [4.05, 0.9], [4.2, 0.62], [4.15, 0.32]],
      wagon: [[0.05, 0.32], [0.02, 0.95], [0.2, 1.48], [3.0, 1.5], [3.55, 1.0], [4.4, 0.9], [4.55, 0.62], [4.5, 0.32]],
      suv: [[0.05, 0.45], [0.02, 1.02], [0.25, 1.58], [3.0, 1.68], [3.5, 1.15], [4.45, 1.05], [4.6, 0.75], [4.55, 0.45]],
      minivan: [[0.05, 0.4], [0.02, 1.1], [0.25, 1.75], [3.2, 1.8], [3.8, 1.15], [4.65, 1.0], [4.8, 0.7], [4.75, 0.4]],
    };
    return P[body] || P.sedan;
  }

  drawHit(S) {
    const { ctx, W, H } = this;
    const cam = S.cam || { span: 9.5, cx: S.x + S.L / 2 + 1 };
    const scale = Math.min(W / cam.span, H / 3.4); // крупно: манекены в салоне должны читаться
    const shakeX = S.shake > 0.02 ? (Math.random() - 0.5) * 10 * S.shake : 0;
    const camX = cam.cx;
    const gy = H * 0.8;
    const sx = (x) => W * 0.5 + (x - camX) * scale + shakeX;
    const sy = (y) => gy - y * scale;

    // небо и дальний лес (параллакс)
    const sky = ctx.createLinearGradient(0, 0, 0, gy);
    sky.addColorStop(0, "#9ec3e6"); sky.addColorStop(1, "#dfe9f0");
    ctx.fillStyle = sky; ctx.fillRect(0, 0, W, gy);
    ctx.fillStyle = "#5b7a5a";
    for (let i = -2; i < 40; i++) {
      const bx = ((i * 2.3 - camX * 0.25) % 92 + 92) % 92 - 6;
      const h = 1.6 + rnd(i) * 1.6;
      ctx.beginPath(); ctx.moveTo(sx(camX + bx - 6) , sy(0.6)); ctx.lineTo(sx(camX + bx - 6 + 1.1), sy(0.6 + h)); ctx.lineTo(sx(camX + bx - 6 + 2.2), sy(0.6)); ctx.fill();
    }
    // дорога
    ctx.fillStyle = "#4a4d53"; ctx.fillRect(0, gy, W, H - gy);
    ctx.fillStyle = "#e9e9e4";
    for (let i = Math.floor((camX - 12) / 6); i < (camX + 12) / 6; i++) ctx.fillRect(sx(i * 6), gy + 10, 3 * scale, 3);

    // лось: из кустов на дальней обочине — резкий прыжок на дорогу
    const mk = S.mooseLeap;
    if (S.mooseState !== "hidden") this.mooseSide(S, sx, sy, scale);
    // кусты переднего плана, за которыми он прятался
    for (let i = Math.floor((camX - 12) / 4); i < (camX + 12) / 4; i++) {
      const x = i * 4 + rnd(i) * 2;
      if (S.mooseState === "hidden" || Math.abs(x - S.mooseX) > 3) this.bushSide(sx(x), gy - 4, (0.55 + rnd(i * 3) * 0.3) * scale, 0.55);
    }
    if (S.mooseState === "hidden") this.bushSide(sx(S.mooseX), gy - 4, 1.0 * scale, 0.9);
    void mk;

    this.carSide(S, sx, sy, scale);
    if (S.phase !== "after") this.hudSpeed(S.v * 3.6);
    this.hudCaption(S);
    if (S.slow) this.badge("Замедленно ×3");
    if (S.phase === "after") this.dummyLegend();
  }

  bushSide(x, y, r, alpha) {
    const { ctx } = this;
    ctx.save(); ctx.globalAlpha = alpha;
    for (let i = 0; i < 3; i++) {
      const g = ctx.createRadialGradient(x + (i - 1) * r * 0.7, y - r * 0.7, r * 0.1, x + (i - 1) * r * 0.7, y - r * 0.5, r);
      g.addColorStop(0, "#5f9a45"); g.addColorStop(1, "#2f5a25");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(x + (i - 1) * r * 0.7, y - r * 0.5, r * (i === 1 ? 1 : 0.8), 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();
  }

  mooseSide(S, sx, sy, s) {
    const { ctx } = this;
    const k = S.mooseLeap;
    let x = S.mooseX, y = 0, rot = 0, legs = 0.35;
    if (S.mooseState === "leap") { y = Math.sin(k * Math.PI) * 0.9; legs = 0.75; }
    if (S.phase === "impact" || S.phase === "after") {
      const t = S.t - S.impactT;
      const carFront = S.x + S.L;
      if (S.r.roof_hit) {
        // низкий капот подбивает лосю ноги — туша опрокидывается на лобовое стекло и крышу
        const q = ease(t / 0.3), slide = ease((t - 0.3) / 0.5);
        x = lerp(S.mooseX, carFront - 1.7, q) - 0.4 * slide;
        y = lerp(0, 0.12, q); rot = lerp(0, 0.3, q); legs = 1;
      } else {
        // высокий капот: удар принимают бампер и капот, лося отбрасывает вперёд, он падает на бок
        const q = ease(t / 0.6);
        x = S.mooseX + 3.5 * q; y = Math.sin(clamp(t / 0.6, 0, 1) * Math.PI) * 0.6 - 0.9 * q * q; rot = lerp(0, -1.3, q); legs = 1;
      }
    }
    ctx.save();
    ctx.translate(sx(x), sy(y + 1.5));
    ctx.rotate(rot);
    ctx.scale(-1, 1); // лось стоит мордой к машине
    // ноги
    ctx.strokeStyle = "#2a1a10"; ctx.lineCap = "round"; ctx.lineWidth = 0.11 * s;
    for (const [lx, ph] of [[0.75, 1], [0.6, -1], [-0.7, -1], [-0.85, 1]]) {
      ctx.beginPath(); ctx.moveTo(lx * s, 0.2 * s);
      ctx.lineTo((lx + ph * legs * 0.35) * s, 1.5 * s); ctx.stroke();
    }
    // тело
    const g = ctx.createLinearGradient(0, -0.5 * s, 0, 0.5 * s);
    g.addColorStop(0, "#6a4529"); g.addColorStop(1, "#3a2414");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.ellipse(0, 0, 1.2 * s, 0.48 * s, 0, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(0.6 * s, -0.25 * s, 0.5 * s, 0.4 * s, 0, 0, Math.PI * 2); ctx.fill(); // холка
    // шея, голова, рога
    ctx.save(); ctx.translate(1.0 * s, -0.25 * s); ctx.rotate(-0.6);
    ctx.fillStyle = "#4a2f1c"; rr(ctx, 0, -0.2 * s, 0.8 * s, 0.4 * s, 0.15 * s); ctx.fill();
    ctx.restore();
    ctx.fillStyle = "#2f1d10";
    ctx.beginPath(); ctx.ellipse(1.65 * s, -0.65 * s, 0.42 * s, 0.17 * s, 0.5, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#cfb88c";
    ctx.beginPath();
    ctx.moveTo(1.4 * s, -0.8 * s); ctx.quadraticCurveTo(1.0 * s, -1.4 * s, 1.55 * s, -1.35 * s);
    ctx.quadraticCurveTo(1.75 * s, -1.05 * s, 1.5 * s, -0.82 * s); ctx.fill();
    ctx.restore();
  }

  carSide(S, sx, sy, s) {
    const { ctx } = this;
    const prof = this.carProfile(this.car.body, S.L);
    const Lp = prof.reduce((m, p) => Math.max(m, p[0]), 0);
    const kx = S.L / Lp;
    // деформация: перёд сминается, крыша продавливается в районе стойки
    const pt = ([px, py]) => {
      let x = px * kx, y = py;
      const fromFront = S.L - x;
      if (fromFront < 1.2) x -= S.crush * (1 - fromFront / 1.2);
      if (py > 1.2 && x > S.L * 0.3 && x < S.L * 0.75) y -= S.roofDent * Math.sin(((x - S.L * 0.3) / (S.L * 0.45)) * Math.PI);
      return [x, y];
    };
    ctx.save();
    // поворот на клевок вокруг передних колёс
    const pivotX = sx(S.x + S.L - 0.95), pivotY = sy(0.33);
    ctx.translate(pivotX, pivotY); ctx.rotate(S.pitch); ctx.translate(-pivotX, -pivotY);
    const P = prof.map(pt).map(([x, y]) => [sx(S.x + x), sy(y + 0.05)]);

    // салон (видно сквозь «разрез»): тёмный фон, сиденья, манекены
    ctx.save();
    ctx.beginPath(); P.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); ctx.clip();
    ctx.fillStyle = "#1c1f25"; ctx.fillRect(sx(S.x) - 10, sy(2.2), S.L * s + 20, 2.3 * s);
    this.seatsAndDummies(S, sx, sy, s);
    ctx.restore();

    // кузов: рисуем «каркас» — пороги, стойки, капот, багажник — салон остаётся видимым
    ctx.lineJoin = "round";
    const paint = this.car.color;
    ctx.strokeStyle = shade(paint, -0.2); ctx.lineWidth = 0.12 * s;
    ctx.beginPath(); P.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); ctx.stroke();
    ctx.fillStyle = paint;
    const sill = (x0, x1, h0, h1) => { // сплошные панели кузова
      ctx.beginPath(); ctx.moveTo(sx(S.x + x0), sy(h0)); ctx.lineTo(sx(S.x + x1), sy(h0));
      ctx.lineTo(sx(S.x + x1), sy(h1)); ctx.lineTo(sx(S.x + x0), sy(h1)); ctx.closePath(); ctx.fill();
    };
    sill(0.05, S.L - 0.05 - S.crush, 0.3, 0.52);                     // нижняя часть дверей и порог
    // капот и багажник — заливкой по силуэту
    ctx.save();
    ctx.beginPath(); P.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); ctx.clip();
    ctx.fillStyle = paint;
    ctx.fillRect(sx(S.x + S.L * 0.76), sy(2), S.L * s, 2 * s);       // моторный отсек
    ctx.fillRect(sx(S.x) - 5, sy(2), S.L * 0.2 * s + 5, 2 * s);      // багажник
    ctx.restore();
    // стойки
    ctx.strokeStyle = shade(paint, -0.15); ctx.lineWidth = 0.09 * s;
    const pillarX = (f) => sx(S.x + S.L * f);
    ctx.beginPath(); ctx.moveTo(pillarX(0.5), sy(0.55)); ctx.lineTo(pillarX(0.5), sy(1.38 - S.roofDent * 0.8)); ctx.stroke();
    // лобовое стекло с трещинами
    if (S.r.roof_hit && S.phase !== "drive" && S.phase !== "brake") {
      const wx = sx(S.x + S.L * 0.7), wy = sy(1.2);
      ctx.strokeStyle = "rgba(255,255,255,.85)"; ctx.lineWidth = 1;
      for (let i = 0; i < 9; i++) {
        const a = (i / 9) * Math.PI * 2;
        ctx.beginPath(); ctx.moveTo(wx, wy); ctx.lineTo(wx + Math.cos(a) * 0.35 * s * (0.6 + rnd(i) * 0.6), wy + Math.sin(a) * 0.3 * s); ctx.stroke();
      }
    }
    // колёса
    for (const wxm of [0.85, S.L - 0.95]) {
      const cx = sx(S.x + wxm), cy = sy(0.33);
      ctx.fillStyle = "#121315"; ctx.beginPath(); ctx.arc(cx, cy, 0.33 * s, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#9aa1ab"; ctx.beginPath(); ctx.arc(cx, cy, 0.18 * s, 0, Math.PI * 2); ctx.fill();
    }
    // фары / стопы
    ctx.fillStyle = "#fff6c8"; const fx = P[P.length - 3];
    ctx.beginPath(); ctx.arc(fx[0] - 4, fx[1] + 6, 0.07 * s, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = S.phase === "brake" || S.phase === "impact" ? "#ff2a2a" : "#8a1010";
    ctx.fillRect(sx(S.x) - 2, sy(0.88), 0.08 * s, 0.14 * s);
    ctx.restore();
  }

  seatsAndDummies(S, sx, sy, s) {
    const { ctx } = this;
    const D = S.dummy;
    const after = S.phase === "after";
    const seats = S.r.seats || {};
    const seat = (hx) => { // спинка и подушка
      ctx.fillStyle = "#3a3f49";
      rr(ctx, sx(S.x + hx - 0.32), sy(0.62), 0.55 * s, 0.12 * s, 4); ctx.fill();
      ctx.save(); ctx.translate(sx(S.x + hx - 0.3), sy(0.62)); ctx.rotate(-0.25);
      rr(ctx, -0.06 * s, -0.75 * s, 0.13 * s, 0.75 * s, 4); ctx.fill(); ctx.restore();
    };
    // руль и подушка безопасности
    const wheel = [S.x + S.L * 0.66, 0.98];
    const dummy = (hx, lean, head, info, alpha = 1, front = true) => {
      ctx.save(); ctx.globalAlpha = alpha;
      const hip = [sx(S.x + hx), sy(0.68)];
      const col = (zone) => (after && info ? SEVERITY[info[zone].level] : "#f2c230");
      // ноги
      ctx.strokeStyle = col("legs"); ctx.lineCap = "round"; ctx.lineWidth = 0.13 * s;
      ctx.beginPath(); ctx.moveTo(hip[0], hip[1]); ctx.lineTo(hip[0] + 0.45 * s, hip[1] - 0.02 * s);
      ctx.lineTo(hip[0] + 0.55 * s, hip[1] + 0.42 * s); ctx.stroke();
      // корпус
      ctx.save(); ctx.translate(hip[0], hip[1]); ctx.rotate(0.22 + lean);
      ctx.fillStyle = col("chest");
      rr(ctx, -0.1 * s, -0.58 * s, 0.22 * s, 0.6 * s, 0.07 * s); ctx.fill();
      // ремень
      ctx.strokeStyle = "#20242b"; ctx.lineWidth = 0.035 * s;
      ctx.beginPath(); ctx.moveTo(-0.1 * s, -0.55 * s); ctx.lineTo(0.12 * s, -0.05 * s); ctx.stroke();
      // рука к рулю
      if (front) {
        ctx.strokeStyle = col("chest"); ctx.lineWidth = 0.08 * s;
        ctx.beginPath(); ctx.moveTo(0.02 * s, -0.48 * s); ctx.lineTo(0.3 * s, -0.36 * s); ctx.lineTo(0.48 * s, -0.42 * s); ctx.stroke();
      }
      // голова (с маркером краш-теста)
      ctx.translate(0, -0.66 * s); ctx.rotate(head);
      ctx.fillStyle = col("head");
      ctx.beginPath(); ctx.arc(0.02 * s, -0.08 * s, 0.11 * s, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#111";
      ctx.beginPath(); ctx.moveTo(0.02 * s, -0.08 * s); ctx.arc(0.02 * s, -0.08 * s, 0.06 * s, 0, Math.PI / 2); ctx.fill();
      ctx.beginPath(); ctx.moveTo(0.02 * s, -0.08 * s); ctx.arc(0.02 * s, -0.08 * s, 0.06 * s, Math.PI, Math.PI * 1.5); ctx.fill();
      ctx.restore();
      ctx.restore();
    };
    seat(S.L * 0.3); seat(S.L * 0.53);
    dummy(S.L * 0.3, D.rear, D.rear * 0.5, seats.rear, 1, false);
    dummy(S.L * 0.53 - 0.05, D.lean * 0.95, D.head * 0.9, seats.passenger, 0.55);   // пассажир — за водителем
    // руль
    ctx.strokeStyle = "#0d0e10"; ctx.lineWidth = 0.06 * s;
    ctx.beginPath(); ctx.moveTo(sx(wheel[0]), sy(wheel[1] - 0.1)); ctx.lineTo(sx(wheel[0] + 0.12), sy(wheel[1] + 0.16)); ctx.stroke();
    dummy(S.L * 0.53, D.lean, D.head, seats.driver);
    if (D.bag > 0) { // подушка безопасности
      ctx.fillStyle = "rgba(245,245,245,.92)";
      ctx.beginPath(); ctx.ellipse(sx(wheel[0] - 0.12 * D.bag), sy(wheel[1] + 0.12), 0.28 * s * D.bag, 0.3 * s * D.bag, 0, 0, Math.PI * 2); ctx.fill();
    }
  }

  dummyLegend() {
    const { ctx, W, H } = this;
    const items = [["low", "ушибы"], ["medium", "средние"], ["high", "тяжёлые"], ["critical", "угроза жизни"]];
    ctx.font = "600 12px system-ui, sans-serif"; ctx.textAlign = "left";
    let x = W - 12 - items.reduce((s, [, t]) => s + ctx.measureText(t).width + 26, 0), y = H - 22; // внизу справа
    rr(ctx, x - 8, y - 14, W - x - 4, 24, 10); ctx.fillStyle = "rgba(10,12,16,.6)"; ctx.fill();
    for (const [k, t] of items) {
      ctx.fillStyle = SEVERITY[k]; ctx.beginPath(); ctx.arc(x + 5, y - 2, 5, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#fff"; ctx.fillText(t, x + 14, y + 2); x += ctx.measureText(t).width + 26;
    }
  }

  // ======================================================================================
  //  HUD
  // ======================================================================================
  hudSpeed(kmh) {
    const { ctx, H } = this;
    const r = Math.min(46, H * 0.12), cx = 14 + r, cy = H - 14 - r * 0.7;
    ctx.save();
    ctx.fillStyle = "rgba(10,12,16,.62)";
    ctx.beginPath(); ctx.arc(cx, cy, r + 6, Math.PI * 0.8, Math.PI * 2.2); ctx.lineTo(cx, cy); ctx.fill();
    const a0 = Math.PI * 0.8, a1 = Math.PI * 2.2, max = 120;
    ctx.strokeStyle = "rgba(255,255,255,.25)"; ctx.lineWidth = 4;
    ctx.beginPath(); ctx.arc(cx, cy, r, a0, a1); ctx.stroke();
    ctx.strokeStyle = "#ffb547";
    ctx.beginPath(); ctx.arc(cx, cy, r, a0, a0 + (a1 - a0) * clamp(kmh / max, 0, 1)); ctx.stroke();
    const a = a0 + (a1 - a0) * clamp(kmh / max, 0, 1);
    ctx.strokeStyle = "#ff4d4d"; ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(a) * (r - 6), cy + Math.sin(a) * (r - 6)); ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.textAlign = "center";
    ctx.font = `700 ${Math.round(r * 0.42)}px system-ui, sans-serif`; ctx.fillText(Math.round(kmh), cx, cy + r * 0.45);
    ctx.font = "600 10px system-ui, sans-serif"; ctx.fillStyle = "#cfd4dc"; ctx.fillText("км/ч", cx, cy + r * 0.72);
    ctx.restore();
  }

  hudRoll(roll) { // вид машины сзади: крен кузова
    const { ctx, W } = this;
    const w = 92, h = 70, x = 0, y = 0;
    const k = clamp(W / 720, 0.62, 1); // на телефоне индикатор меньше
    ctx.save();
    ctx.translate(W - 12 - w * k, 12); ctx.scale(k, k);
    ctx.save();
    rr(ctx, x, y, w, h, 12); ctx.fillStyle = "rgba(10,12,16,.62)"; ctx.fill();
    const deg = Math.abs(roll) * 180 / Math.PI;
    const color = deg > 25 ? "#ff3b3b" : deg > 12 ? "#ffb547" : "#3ecf8e";
    ctx.translate(x + w / 2, y + 48);
    ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(-36, 6); ctx.lineTo(36, 6); ctx.stroke();
    ctx.rotate(roll);
    ctx.translate(roll > 0.25 ? -20 : roll < -0.25 ? 20 : 0, 0);
    ctx.fillStyle = "#121315"; rr(ctx, -26, -2, 9, 9, 2); ctx.fill(); rr(ctx, 17, -2, 9, 9, 2); ctx.fill();
    ctx.fillStyle = this.car.color; rr(ctx, -27, -18, 54, 18, 5); ctx.fill();
    ctx.fillStyle = shade(this.car.color, -0.2); rr(ctx, -18, -30, 36, 14, 5); ctx.fill();
    ctx.fillStyle = "#2b3a4a"; rr(ctx, -14, -27, 28, 9, 3); ctx.fill();
    ctx.restore();
    ctx.fillStyle = color; ctx.font = "700 12px system-ui, sans-serif"; ctx.textAlign = "center";
    ctx.fillText(`крен ${deg.toFixed(0)}°`, x + w / 2, y + h - 4 + 14);
    ctx.restore();
  }

  hudCaption(S) {
    if (!S.caption) return;
    const { ctx, W } = this;
    const age = S.real - S.captionT;
    const alpha = clamp(1.6 - age * 0.6, 0, 1);
    if (alpha <= 0) return;
    const alarm = /ЛОСЬ|Занос|два колеса|Удар/.test(S.caption);
    ctx.save(); ctx.globalAlpha = alpha;
    ctx.font = `800 ${alarm ? 22 : 17}px system-ui, sans-serif`; ctx.textAlign = "center";
    const tw = ctx.measureText(S.caption).width + 28;
    const pop = alarm ? 1 + Math.max(0, 0.25 - age) : 1;
    ctx.translate(W / 2, 30); ctx.scale(pop, pop);
    rr(ctx, -tw / 2, -18, tw, 34, 17); ctx.fillStyle = alarm ? "rgba(220,38,38,.9)" : "rgba(10,12,16,.7)"; ctx.fill();
    ctx.fillStyle = "#fff"; ctx.fillText(S.caption, 0, 6);
    ctx.restore();
  }

  badge(text) {
    const { ctx, H, W } = this;
    ctx.save();
    ctx.font = "700 12px system-ui, sans-serif"; ctx.textAlign = "right";
    const tw = ctx.measureText(text).width + 18;
    rr(ctx, W - tw - 12, H - 36, tw, 24, 12); ctx.fillStyle = "rgba(255,181,71,.92)"; ctx.fill();
    ctx.fillStyle = "#111"; ctx.fillText(text, W - 21, H - 19);
    ctx.restore();
  }
}
