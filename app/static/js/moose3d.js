// «Тест на лося» в 3D — внутри той же сцены, что и экскурсия (viewer.js): та же улица, та же машина.
// Исход (объезд: pass / limit / fail+skid / fail+rollover; удар: уровень, roof_hit, травмы по местам)
// приходит из API (/cars/{id}/moose) — здесь только показ. Анимация детерминирована (seed).
//
//   StreetLoop — бесконечная улица из исходного квартала: копии обрезаются плоскостями по чистым
//                границам [A; B] и чередуются с зеркальными, так что каждый стык симметричен.
//   Dummy      — процедурный манекен (капсулы, суставы, маркеры краш-теста), пружинно-демпферная
//                физика корпуса и головы, руки на руле (двухзвенная IK).
//   Crumple    — смятие вершин переда и крыши (на копиях геометрии, исходная восстанавливается).
//   MooseTest  — сценарии, лось (elk.glb + его клипы), обломки, осколки, следы шин, камеры,
//                запись кадров и настоящий повтор с камеры сзади ×3.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { ENVIRONMENT } from "./viewer.js";

const ELK_URL = "/static/media/moose/elk.glb";
const DRACO_PATH = "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/libs/draco/gltf/";
const G = 9.81;
const BRAKE = 7.85;       // замедление при экстренном торможении, м/с²
const LEAP_TIME = 0.19;   // прыжок лося из кустов на полосу, с
const LEAP_FROM = -3.2;   // откуда прыгает: за кустом у края дороги, м вправо от оси полосы
const MAX_STEP = 1 / 240; // самый крупный подшаг симуляции, с
const SEV = { low: 0x3ecf8e, medium: 0xffd23f, high: 0xff8c2a, critical: 0xff3b3b };
const UP = new THREE.Vector3(0, 1, 0);
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const lerp = (a, b, t) => a + (b - a) * t;
const ease = (t) => 1 - Math.pow(1 - clamp(t, 0, 1), 3);
const smooth = (t) => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };

function rng(seed) { // mulberry32 — одинаковый seed даёт одинаковые обломки
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

let elkPromise = null; // 6 МБ — грузим только при первом открытии теста
function loadElk() {
  elkPromise ??= new GLTFLoader().setDRACOLoader(new DRACOLoader().setDecoderPath(DRACO_PATH)).loadAsync(ELK_URL);
  return elkPromise;
}

const materialsOf = (o) => (Array.isArray(o.material) ? o.material : [o.material]);

/** Отрезок-цилиндр (высота 1 по Y) между двумя точками в системе его родителя. */
function setSegment(mesh, a, b, thickness) {
  const d = b.clone().sub(a);
  const len = Math.max(1e-4, d.length());
  mesh.position.copy(a).addScaledVector(d, 0.5);
  mesh.quaternion.setFromUnitVectors(UP, d.multiplyScalar(1 / len));
  mesh.scale.set(thickness, len, thickness);
}

/** Двухзвенная IK: плечо S, цель T, длины a, b, направление «локтя» pole → позиция локтя. */
function solveElbow(S, T, a, b, pole) {
  const dir = T.clone().sub(S);
  const d = clamp(dir.length(), 1e-3, a + b - 1e-3);
  dir.normalize();
  const x = (a * a - b * b + d * d) / (2 * d);
  const h = Math.sqrt(Math.max(0, a * a - x * x));
  const p = pole.clone().addScaledVector(dir, -pole.dot(dir)).normalize();
  return S.clone().addScaledVector(dir, x).addScaledVector(p, h);
}

// ======================================================================================
//  Бесконечная улица
// ======================================================================================
class StreetLoop {
  constructor(viewer) {
    this.v = viewer;
    // чистые границы квартала: от выхода с перекрёстка до края асфальта (там же стык экскурсии)
    this.A = -10;
    this.B = ENVIRONMENT.mirrorAt.x;
    this.L = this.B - this.A;
    this.buildColliders();
    this.units = [];
    for (let k = -1; k <= 4; k++) this.units.push(this.makeUnit(k));
  }

  /** Препятствия квартала (фонари, стволы, скамейки) — по «подошве»: вершинам у самой земли.
   *  Координаты — в системе квартала; для копии k их переводит worldX(). */
  buildColliders() {
    const st = this.v.street;
    st.updateMatrixWorld(true);
    const toLocal = new THREE.Matrix4().copy(st.matrix).multiply(new THREE.Matrix4().copy(st.matrixWorld).invert());
    const meshes = [];
    st.traverse((o) => o.isMesh && meshes.push(o));
    const M = new THREE.Matrix4(), p = new THREE.Vector3();
    const baseName = (n) => n.replace(/_\d+$/, "");
    this.colliders = [];
    meshes.forEach((o) => {
      M.multiplyMatrices(toLocal, o.matrixWorld);
      const pos = o.geometry.attributes.position;
      const pts = [];
      let minY = Infinity, maxY = -Infinity;
      for (let i = 0; i < pos.count; i++) {
        p.fromBufferAttribute(pos, i).applyMatrix4(M);
        pts.push(p.x, p.y, p.z);
        minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
      }
      if (maxY - minY < 0.9 || minY > 0.35) return;          // не стоит на земле или слишком низкое
      let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
      for (let i = 0; i < pts.length; i += 3) if (pts[i + 1] < minY + 0.5) {
        x0 = Math.min(x0, pts[i]); x1 = Math.max(x1, pts[i]); z0 = Math.min(z0, pts[i + 2]); z1 = Math.max(z1, pts[i + 2]);
      }
      const w = x1 - x0, d = z1 - z0, cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
      if (w > 2.5 || d > 2.5 || cx < this.A - 0.5 || cx > this.B + 0.5) return; // здания и чужие кварталы
      const kind = /^circle/i.test(o.name) ? "lamp" : /^tree/i.test(o.name) ? "tree" : "object";
      // длинное (скамейка) — цепочка кругов вдоль длинной стороны
      const along = w >= d, len = Math.max(w, d), r = Math.max(0.08, Math.min(w, d) / 2 + 0.03);
      const n = len > 0.6 ? Math.ceil(len / 0.45) : 1, circles = [];
      for (let j = 0; j < n; j++) {
        if (n === 1) { circles.push([cx, cz, r]); break; }
        const t = j / (n - 1);
        circles.push(along ? [lerp(x0 + r, x1 - r, t), cz, r] : [cx, lerp(z0 + r, z1 - r, t), r]);
      }
      const base = baseName(o.name);
      const tilt = kind === "lamp" ? meshes.map((m, i) => (baseName(m.name) === base ? i : -1)).filter((i) => i >= 0) : [];
      this.colliders.push({ kind, circles, tilt });
    });
  }

  worldX(u, x) { return u.k % 2 === 0 ? x + u.k * this.L : 2 * this.B + (u.k - 1) * this.L - x; }

  makeUnit(k) {
    const planes = [new THREE.Plane(new THREE.Vector3(1, 0, 0), 0), new THREE.Plane(new THREE.Vector3(-1, 0, 0), 0)];
    const inner = this.v.street.clone(true);
    inner.traverse((o) => {
      if (!o.isMesh) return;
      const mats = materialsOf(o).map((m) => { const c = m.clone(); c.clippingPlanes = planes; return c; });
      o.material = mats.length === 1 ? mats[0] : mats;
    });
    const wrap = new THREE.Group();
    wrap.name = "moose_street_unit";
    wrap.add(inner);
    wrap.visible = false;
    this.v.scene.add(wrap);
    const meshes = [];
    inner.traverse((o) => o.isMesh && meshes.push(o));   // тот же порядок, что в buildColliders
    const u = { k: null, wrap, planes, meshes };
    this.place(u, k);
    return u;
  }

  /** Копия k занимает [A + kL; B + kL]: чётные — сдвиг оригинала, нечётные — зеркальные. */
  place(u, k) {
    const { A, B, L } = this;
    u.k = k;
    if (k % 2 === 0) { u.wrap.position.x = k * L; u.wrap.scale.x = 1; }
    else { u.wrap.position.x = 2 * B + (k - 1) * L; u.wrap.scale.x = -1; }
    u.planes[0].constant = -(A + k * L - 0.02); // x ≥ A + kL
    u.planes[1].constant = B + k * L + 0.02;    // x ≤ B + kL
    u.wrap.updateMatrixWorld(true);
  }

  update(x) {
    const lo = Math.floor((x - 35 - this.A) / this.L), hi = Math.floor((x + 90 - this.A) / this.L);
    for (const u of this.units) {
      while (u.k < lo) this.place(u, u.k + 6);
      while (u.k > lo + 5) this.place(u, u.k - 6);
      u.wrap.visible = this.shown && u.k <= hi;
    }
  }

  show(on, x = ENVIRONMENT.carSpot.x) {
    this.shown = on;
    this.v.environment.visible = !on;
    if (on) this.update(x); else this.units.forEach((u) => (u.wrap.visible = false));
  }
}

// ======================================================================================
//  Манекен — деревянный шарнирный манекен (wooden_artist_mannequin.glb, Smoggybeard / Sketchfab),
//  разобранный tools/prepare_mannequin.py на сегменты с шарнирами в шарах-суставах.
// ======================================================================================
const MANNEQUIN_URL = "/static/media/moose/mannequin.glb";
let mannequinPromise = null;
function loadMannequin() {
  mannequinPromise ??= new GLTFLoader().loadAsync(MANNEQUIN_URL).then((g) => {
    const seg = {};
    g.scene.traverse((o) => { if (o.isMesh) seg[o.name] = o; });
    return seg;
  });
  return mannequinPromise;
}
// шарниры в системе манекена (Y вверх, перёд −Z), как в prepare_mannequin.py
const JOINT = {
  neck: [0, 0.21, 0], waist: [0, -0.088, 0], hipC: [0, -0.36, 0],
  shoulder: [0.153, 0.171, 0], elbow: [0.257, -0.105, 0], wrist: [0.272, -0.36, 0],
  hip: [0.066, -0.36, 0], knee: [0.066, -0.787, 0], ankle: [0.066, -1.207, 0],
};
const jv = (name, sx = 1) => new THREE.Vector3(JOINT[name][0] * sx, JOINT[name][1], JOINT[name][2]);
const RECLINE = 0.32;          // наклон спинки сиденья, рад

class Dummy {
  /** seg — сегменты из mannequin.glb; hip — центр таза в системе машины; wheel — руль (для рук водителя) */
  constructor(name, seg, hip, { front = true, wheel = null } = {}) {
    this.name = name; this.front = front; this.wheel = wheel;
    const base = seg.chest.material;
    this.mat = { head: base.clone(), chest: base.clone(), legs: base.clone() };
    this.dark = new THREE.MeshStandardMaterial({ color: 0x15171c, roughness: 0.7 });
    const zone = (id) => (id === "head" ? "head" : /thigh|shin|foot/.test(id) ? "legs" : "chest");
    const piece = (id, parent, offset) => {
      const g = new THREE.Group();
      g.name = `dummy_${id}`;
      g.position.copy(offset);
      const m = new THREE.Mesh(seg[id].geometry, this.mat[zone(id)]);
      m.castShadow = false;
      g.add(m);
      parent.add(g);
      return g;
    };
    this.root = new THREE.Group();
    this.root.name = `dummy_${name}`;
    this.root.position.copy(hip);
    const H = jv("hipC"), W = jv("waist");
    piece("pelvis", this.root, W.clone().sub(H));
    this.chest = piece("chest", this.root, W.clone().sub(H));
    this.head = piece("head", this.chest, jv("neck").sub(W));
    this.arms = []; this.legs = [];
    for (const sx of [1, -1]) {
      const s = sx > 0 ? "p" : "n";
      const upper = piece(`upperarm_${s}`, this.chest, jv("shoulder", sx).sub(W));
      const fore = piece(`forearm_${s}`, upper, jv("elbow", sx).sub(jv("shoulder", sx)));
      piece(`hand_${s}`, fore, jv("wrist", sx).sub(jv("elbow", sx)));
      this.arms.push({ sx, upper, fore,
        upperRest: jv("elbow", sx).sub(jv("shoulder", sx)).normalize(), foreRest: jv("wrist", sx).sub(jv("elbow", sx)).normalize(),
        a: jv("elbow", sx).distanceTo(jv("shoulder", sx)), b: jv("wrist", sx).distanceTo(jv("elbow", sx)) + 0.04 });
      const thigh = piece(`thigh_${s}`, this.root, jv("hip", sx).sub(H));
      const shin = piece(`shin_${s}`, thigh, jv("knee", sx).sub(jv("hip", sx)));
      const foot = piece(`foot_${s}`, shin, jv("ankle", sx).sub(jv("knee", sx)));
      thigh.rotation.set(1.42, 0, sx * 0.06);      // сидит: бёдра вперёд, голени вниз
      shin.rotation.x = -1.3;
      foot.rotation.x = -0.1;
      this.legs.push(thigh);
    }
    // ремень: плечо у двери → противоположное бедро, и поясная лямка
    this.belt = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), this.dark);
    this.lap = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), this.dark);
    this.root.add(this.belt, this.lap);
    if (front) {
      this.bag = new THREE.Mesh(new THREE.SphereGeometry(0.24, 18, 12),
        new THREE.MeshStandardMaterial({ color: 0xf4f4f2, roughness: 0.9, transparent: true, opacity: 0.94 }));
      this.bag.scale.setScalar(0.001); this.bag.visible = false;
    }
    this.reset();
  }

  setScale(k) {
    this.k = k;
    this.root.scale.setScalar(k);
    this.applyPose(0);
  }

  reset() {
    this.th = 0; this.thV = 0; this.ph = 0; this.phV = 0; this.psi = 0;
    this.limit = this.front ? 0.5 : 0.75;
    for (const k of ["head", "chest", "legs"]) this.mat[k].color.setRGB(1, 1, 1);
    if (this.bag) { this.bag.visible = false; this.bag.scale.setScalar(0.001); }
    this.applyPose(0);
  }

  /** Физика корпуса и головы: aLong — продольное ускорение машины (м/с², торможение < 0), aLat — боковое */
  physics(dt, aLong, aLat) {
    const n = Math.max(1, Math.ceil(dt / 0.004)), h = dt / n;
    for (let i = 0; i < n; i++) {
      let acc = -60 * this.th - 9 * this.thV - aLong * 0.9;             // инерция: при торможении тянет вперёд
      if (this.th > this.limit) acc -= 900 * (this.th - this.limit) + 30 * this.thV; // ремень / подушка
      this.thV += acc * h; this.th = clamp(this.th + this.thV * h, -0.25, this.limit + 0.12);
      const accH = -80 * this.ph - 7 * this.phV - aLong * 0.45 - acc * 0.06;  // голова — хлыст относительно корпуса
      this.phV += accH * h; this.ph = clamp(this.ph + this.phV * h, -0.6, 0.75);
    }
    this.psi = lerp(this.psi, clamp(aLat * 0.035, -0.3, 0.3), 1 - Math.exp(-dt * 8));
  }

  /** Поворачивает сегмент так, чтобы его ось rest (в своей системе) смотрела на точку target (мир). */
  aim(node, rest, target) {
    const p = node.getWorldPosition(new THREE.Vector3());
    const q = node.parent.getWorldQuaternion(new THREE.Quaternion()).invert();
    const d = target.clone().sub(p).applyQuaternion(q).normalize();
    node.quaternion.setFromUnitVectors(rest, d);
    node.updateMatrixWorld(true);
  }

  applyPose(steer) {
    this.chest.rotation.set(RECLINE - this.th, 0, this.psi, "YXZ");
    this.head.rotation.set(-0.12 - this.ph, 0, 0);
    this.root.updateMatrixWorld(true);
    const car = this.root.parent;
    for (const arm of this.arms) {
      let T;
      if (this.wheel) { // руки на руле «на 10 и 2», руль поворачивается — руки за ним
        const { C, R, U, radius } = this.wheel;
        const ang = (arm.sx < 0 ? Math.PI * 0.8 : Math.PI * 0.2) + steer;
        T = C.clone().addScaledVector(R, Math.cos(ang) * radius).addScaledVector(U, Math.sin(ang) * radius);
      } else {
        T = this.root.position.clone().add(new THREE.Vector3(arm.sx * 0.13, 0.1, -0.3));  // руки на бёдрах
      }
      const Tw = car ? car.localToWorld(T.clone()) : T;
      const S = arm.upper.getWorldPosition(new THREE.Vector3());
      const pole = new THREE.Vector3(arm.sx * 0.7, -1, 0.3);
      if (car) pole.transformDirection(car.matrixWorld);
      const E = solveElbow(S, Tw, arm.a * (this.k || 1), arm.b * (this.k || 1), pole);
      this.aim(arm.upper, arm.upperRest, E);
      this.aim(arm.fore, arm.foreRest, Tw);
    }
    // ремень: от плеча у двери к противоположному бедру
    const outer = this.name === "passenger" ? 1 : -1;
    const toRoot = (v) => this.root.worldToLocal(v);
    const sh = toRoot(this.chest.localToWorld(jv("shoulder", outer).sub(jv("waist")).add(new THREE.Vector3(-outer * 0.03, 0.02, -0.12))));
    const hp = new THREE.Vector3(-outer * 0.14, 0.04, -0.13);
    setSegment(this.belt, sh, hp, 1);
    this.belt.scale.set(0.06, this.belt.scale.y, 0.012);
    setSegment(this.lap, new THREE.Vector3(-0.15, 0.05, -0.13), new THREE.Vector3(0.15, 0.05, -0.13), 1);
    this.lap.scale.set(0.05, this.lap.scale.y, 0.012);
  }

  /** k = 0..1 — плавная окраска зон по тяжести (после удара) */
  colorize(seat, k) {
    if (!seat) return;
    for (const zone of ["head", "chest", "legs"]) {
      const lvl = seat[zone]?.level || "low";
      this.mat[zone].color.setRGB(1, 1, 1).lerp(new THREE.Color(SEV[lvl]), k);
    }
  }
}

// ======================================================================================
//  Смятие кузова
// ======================================================================================
class Crumple {
  constructor() { this.items = []; }

  /** Готовит меши: копия геометрии + целевые (смятые) координаты. Исходная геометрия сохраняется. */
  prepare(meshes, carModel, frontZ, roofY, { crush, dent, dentZ0, dentZ1 }, rand) {
    const toCar = new THREE.Matrix4(), toMesh = new THREE.Matrix4(), v = new THREE.Vector3();
    const carInv = new THREE.Matrix4().copy(carModel.matrixWorld).invert();
    for (const m of meshes) {
      if (m.userData.crumple) continue;
      const orig = m.geometry;
      const geo = orig.clone();
      const pos = geo.attributes.position;
      const from = new Float32Array(pos.array);
      const to = new Float32Array(pos.array);
      toCar.multiplyMatrices(carInv, m.matrixWorld);
      toMesh.copy(toCar).invert();
      for (let i = 0; i < pos.count; i++) {
        v.fromBufferAttribute(pos, i).applyMatrix4(toCar);           // в систему машины (перёд = −Z)
        const fromFront = v.z - frontZ;
        if (fromFront < 1.15) {                                        // зона смятия ~1,15 м
          const k = Math.pow(1 - fromFront / 1.15, 1.4);
          v.z += crush * k;
          v.y -= crush * 0.25 * k * (0.5 + rand());
          v.x *= 1 + crush * 0.08 * k;
        }
        if (dent > 0 && v.y > roofY && v.z > dentZ0 && v.z < dentZ1) {  // крыша над передним рядом
          const t = (v.z - dentZ0) / (dentZ1 - dentZ0);
          v.y -= dent * Math.sin(t * Math.PI) * (0.8 + 0.4 * rand());
        }
        v.applyMatrix4(toMesh);
        to[i * 3] = v.x; to[i * 3 + 1] = v.y; to[i * 3 + 2] = v.z;
      }
      // двигаются только вершины в зоне смятия — их и храним; нормали считаем один раз для k=1 и смешиваем
      const moved = [];
      for (let i = 0; i < pos.count; i++) {
        if (Math.abs(to[i * 3] - from[i * 3]) + Math.abs(to[i * 3 + 1] - from[i * 3 + 1]) + Math.abs(to[i * 3 + 2] - from[i * 3 + 2]) > 1e-5) moved.push(i);
      }
      if (!moved.length) { geo.dispose(); continue; }
      const hasN = !!geo.attributes.normal;
      if (!hasN) geo.computeVertexNormals();
      const nFrom = new Float32Array(geo.attributes.normal.array);
      pos.array.set(to); geo.computeVertexNormals();
      const nTo = new Float32Array(geo.attributes.normal.array);
      pos.array.set(from); geo.attributes.normal.array.set(nFrom);
      m.geometry = geo;
      m.userData.crumple = { orig, geo, from, to, nFrom, nTo, moved: Uint32Array.from(moved) };
      this.items.push(m);
    }
    this.k = 0;
  }

  set(k) {
    if (Math.abs(k - (this.k ?? 0)) < 1e-4) return;
    for (const m of this.items) {
      const { geo, from, to, nFrom, nTo, moved } = m.userData.crumple;
      const p = geo.attributes.position.array, n = geo.attributes.normal.array;
      for (const i of moved) {
        for (let j = i * 3; j < i * 3 + 3; j++) { p[j] = from[j] + (to[j] - from[j]) * k; n[j] = nFrom[j] + (nTo[j] - nFrom[j]) * k; }
      }
      geo.attributes.position.needsUpdate = true;
      geo.attributes.normal.needsUpdate = true;
      geo.computeBoundingSphere();
    }
    this.k = k;
  }

  restore() {
    for (const m of this.items) {
      const c = m.userData.crumple;
      m.geometry = c.orig;   // исходная геометрия общая с кэшем модели — её мы не трогали
      c.geo.dispose();
      delete m.userData.crumple;
    }
    this.items = []; this.k = 0;
  }
}

// ======================================================================================
//  Тест
// ======================================================================================
export class MooseTest {
  /** viewer — CarViewer из viewer.js; overlay — #moose-overlay (туда кладётся HUD) */
  constructor(viewer, overlay) {
    this.v = viewer;
    this.overlay = overlay;
    this.bottomInset = 0;
    this.crumple = new Crumple();
    this.makeHud();
  }

  get active() { return this.v.test === this; }

  // ---------- вход / выход ----------
  async enter() {
    const v = this.v;
    await v.envReady;
    if (!v.car || !v.street) throw new Error("Сцена ещё не готова");
    this.saved = { p: v.p, targetP: v.targetP, yaw: v.yaw, pitch: v.pitch };
    this.orbitOffset = null;
    v.setHover(null);
    this.resetCarPose();
    this.captureCar();
    this.loop ??= new StreetLoop(v);
    this.loop.show(true);
    this.ensureSkid();
    this.ensureBush();
    this.run = null; this.leaving = null; this.clock = 0;
    // камера плавно перелетает из ракурса экскурсии — сцена не «сбрасывается»
    const look = v.camera.position.clone().add(new THREE.Vector3(0, 0, -10).applyQuaternion(v.camera.quaternion));
    this.cam = { pos: v.camera.position.clone(), look, shake: 0 };
    this.fly = { t: 0, dur: 1.2, fromPos: this.cam.pos.clone(), fromLook: look };
    v.test = this;
    this.say("Загружаем лося и манекены…", true);
    const [, seg] = await Promise.all([this.ensureElk(), loadMannequin()]);
    this.seg = seg;
    if (!this.active) return;            // закрыли, пока грузилось
    this.ensureDummies();
    this.say("Выберите скорость и сценарий", true);
  }

  exit() {
    const v = this.v;
    if (!this.active) return;
    this.finish(true);
    this.cleanupRun();
    this.loop?.show(false);
    this.dummies?.forEach((d) => { d.root.removeFromParent(); d.bag?.removeFromParent(); });
    this.bush && (this.bush.visible = false);
    this.restoreSteering();
    this.resetCarPose();
    this.setBottomInset(0);
    Object.assign(v, this.saved);
    // плавно возвращаем камеру в ракурс экскурсии, потом управление снова у viewer
    const pose = v.poseAt(v.p);
    this.leaving = { t: 0, dur: 0.9, fromPos: v.camera.position.clone(), fromLook: this.cam.look.clone(), toPos: pose.pos, toLook: pose.target };
    this.hud.root.hidden = true;
  }

  resetCarPose() {
    const s = ENVIRONMENT.carSpot, v = this.v;
    v.anim = null;
    v.carRoot.position.set(s.x, s.y, s.z);
    v.carRoot.rotation.set(0, s.yaw, 0);
    if (v.car) { v.car.position.set(0, 0, 0); v.car.rotation.set(0, 0, 0); v.car.scale.setScalar(1); }
    v.carRoot.updateMatrixWorld(true);
  }

  /** Геометрия машины в её собственной системе (перёд −Z, водитель −X): габариты, сиденья, руль, колёса. */
  captureCar() {
    const v = this.v, car = v.car;
    if (this.carKey === car.uuid) return;
    this.carKey = car.uuid;
    car.updateMatrixWorld(true);
    const inv = new THREE.Matrix4().copy(car.matrixWorld).invert();
    const localBox = (id) => {
      const wb = v.boxOfPart(id);
      if (!wb) return null;
      const b = new THREE.Box3();
      for (const x of [wb.min.x, wb.max.x]) for (const y of [wb.min.y, wb.max.y]) for (const z of [wb.min.z, wb.max.z]) {
        b.expandByPoint(new THREE.Vector3(x, y, z).applyMatrix4(inv));
      }
      return b;
    };
    const all = new THREE.Box3().setFromObject(car); // мир → машина
    const box = new THREE.Box3();
    for (const x of [all.min.x, all.max.x]) for (const y of [all.min.y, all.max.y]) for (const z of [all.min.z, all.max.z]) box.expandByPoint(new THREE.Vector3(x, y, z).applyMatrix4(inv));
    const size = box.getSize(new THREE.Vector3());
    this.geo = {
      box, halfLen: size.z / 2, halfW: size.x / 2, height: size.y,
      seat: { driver: localBox("seat_driver"), passenger: localBox("seat_passenger"), rear: localBox("seat_rear") },
      wheelBox: localBox("steering_wheel"), dash: localBox("dashboard"), windshield: localBox("windshield"), roof: localBox("roof"),
      wheels: ["wheel_front_left", "wheel_front_right", "wheel_rear_left", "wheel_rear_right"].map((id) => {
        const b = localBox(id); return b ? new THREE.Vector3((b.min.x + b.max.x) / 2, b.min.y + 0.02, (b.min.z + b.max.z) / 2) : null;
      }).filter(Boolean),
    };
    this.dummies?.forEach((d) => d.root.removeFromParent());
    this.dummies = null;
    this.steerMeshes = null;
  }

  // ---------- реквизит ----------
  async ensureElk() {
    if (this.elk) { this.elk.visible = false; return; }
    const g = await loadElk();
    const elk = g.scene;
    elk.traverse((o) => { if (o.isMesh) { o.castShadow = false; o.frustumCulled = false; } });
    elk.updateMatrixWorld(true);
    // куда смотрит морда в исходной позе (по кости головы) и масштаб: высота с головой ≈ 2,45 м
    const b = new THREE.Box3().setFromObject(elk);
    const head = elk.getObjectByName("RigHead_00") || elk.getObjectByName("RigNeck3_021");
    const center = b.getCenter(new THREE.Vector3());
    const hp = head ? head.getWorldPosition(new THREE.Vector3()) : center.clone().add(new THREE.Vector3(0, 0, 1));
    const fwd = hp.sub(center).setY(0).normalize();
    this.elkFwdAngle = Math.atan2(fwd.x, fwd.z);
    this.elkScale = 2.45 / Math.max(0.01, b.max.y - b.min.y);
    this.elkFoot = b.min.y;
    this.elkInner = elk;
    this.elk = new THREE.Group();        // внешняя группа: мы двигаем её, клипы двигают кости внутри
    this.elk.name = "moose_elk";
    elk.scale.setScalar(this.elkScale);
    elk.position.y = -this.elkFoot * this.elkScale;
    this.elk.add(elk);
    this.elk.visible = false;
    this.v.scene.add(this.elk);
    // root motion: Sprint/Run/Death двигают кость RigRoot_01 на метры за цикл (на повторе цикла —
    // телепорт назад). Положение лося задаём сами, поэтому горизонтальный сдвиг корня обнуляем.
    for (const clip of g.animations || []) {
      for (const tr of clip.tracks) {
        if (!/RigRoot_01\.position$/.test(tr.name)) continue;
        const v = tr.values;
        for (let i = 0; i < v.length; i += 3) { v[i] = v[0]; v[i + 2] = v[2]; }
      }
    }
    this.elkBones = [];
    elk.traverse((o) => { if (o.isBone) this.elkBones.push(o); });
    this.mixer = new THREE.AnimationMixer(elk);
    this.actions = Object.fromEntries((g.animations || []).map((c) => [c.name, this.mixer.clipAction(c)]));
  }

  playClip(name, { once = false, fade = 0.15, timeScale = 1 } = {}) {
    const a = this.actions?.[name];
    if (!a || this.clipName === name) return;
    const prev = this.actions[this.clipName];
    a.reset();
    a.setLoop(once ? THREE.LoopOnce : THREE.LoopRepeat, Infinity);
    a.clampWhenFinished = once;
    a.timeScale = timeScale;
    a.play();
    if (prev) prev.crossFadeTo(a, fade, false);
    this.clipName = name;
  }

  ensureDummies() {
    if (this.dummies) { this.dummies.forEach((d) => { this.v.car.add(d.root); d.bag && this.v.car.add(d.bag); d.reset(); }); return; }
    const g = this.geo, car = this.v.car;
    // Верх подушки — самая высокая вершина сиденья в передних 40 % его длины (сзади спинка и подголовник).
    const inv = new THREE.Matrix4().copy(car.matrixWorld).invert(), M = new THREE.Matrix4(), p = new THREE.Vector3();
    const cushionTop = (id, b) => {
      let top = -Infinity;
      const zCut = b.min.z + (b.max.z - b.min.z) * 0.4;
      for (const m of this.v.partMeshes.get(id) || []) {
        M.multiplyMatrices(inv, m.matrixWorld);
        const pos = m.geometry.attributes.position;
        for (let i = 0; i < pos.count; i++) {
          p.fromBufferAttribute(pos, i).applyMatrix4(M);
          if (p.z < zCut && p.x > b.min.x && p.x < b.max.x && p.y > top) top = p.y;
        }
      }
      return Number.isFinite(top) ? top : b.min.y + (b.max.y - b.min.y) * 0.3;
    };
    // тазобедренный шарнир — на 4 см над подушкой, ближе к спинке
    const hipOf = (id, b) => new THREE.Vector3((b.min.x + b.max.x) / 2, cushionTop(id, b) + 0.04, b.max.z - (b.max.z - b.min.z) * 0.36);
    const wb = g.wheelBox;
    let wheel = null;
    if (wb) {
      const C = wb.getCenter(new THREE.Vector3());
      const Ax = new THREE.Vector3(0, 0.35, 1).normalize();                  // ось руля — к водителю и чуть вверх
      const R = new THREE.Vector3(1, 0, 0);
      const U = new THREE.Vector3().crossVectors(Ax, R).normalize();
      if (U.y < 0) U.negate();
      wheel = { C, Ax, R, U, radius: Math.max(wb.max.x - wb.min.x, wb.max.y - wb.min.y) * 0.45 };
    }
    this.wheel = wheel;
    const seats = g.seat;
    this.dummies = [];
    if (seats.driver) this.dummies.push(new Dummy("driver", this.seg, hipOf("seat_driver", seats.driver), { front: true, wheel }));
    if (seats.passenger) this.dummies.push(new Dummy("passenger", this.seg, hipOf("seat_passenger", seats.passenger), { front: true }));
    if (seats.rear) { const b = seats.rear; this.dummies.push(new Dummy("rear", this.seg, hipOf("seat_rear", new THREE.Box3(new THREE.Vector3(b.min.x, b.min.y, b.min.z), new THREE.Vector3((b.min.x + b.max.x) / 2 - 0.05, b.max.y, b.max.z))), { front: false })); }
    // рост манекена — под потолок этой машины (модели машин нормированы по длине, салоны разные)
    const roofIn = (g.roof ? g.roof.min.y : g.box.max.y - 0.1) - 0.05;
    for (const d of this.dummies) {
      car.add(d.root);
      d.applyPose(0);
      const hb = new THREE.Box3().setFromObject(d.head);
      let headTop = -Infinity;
      for (const x of [hb.min.x, hb.max.x]) for (const y of [hb.min.y, hb.max.y]) for (const z of [hb.min.z, hb.max.z]) headTop = Math.max(headTop, p.set(x, y, z).applyMatrix4(inv).y);
      d.setScale(clamp((roofIn - d.root.position.y) / (headTop - d.root.position.y), 0.72, 1));
      if (d.bag) {
        car.add(d.bag);
        if (d.name === "driver" && wheel) d.bag.position.copy(wheel.C).addScaledVector(wheel.Ax, 0.16);
        else if (g.dash) d.bag.position.set(d.root.position.x, g.dash.max.y - 0.02, g.dash.max.z + 0.12);
        else d.bag.position.copy(d.root.position).add(new THREE.Vector3(0, 0.45, -0.55));
      }
    }
    this.prepareSteering();
  }

  /** Руль поворачивается вокруг своей оси (матрица меша, исходная сохраняется). */
  prepareSteering() {
    const v = this.v;
    if (!this.wheel || this.steerMeshes) return;
    this.steerMeshes = (v.partMeshes.get("steering_wheel") || []).map((m) => {
      const toParent = new THREE.Matrix4().copy(m.parent.matrixWorld).invert().multiply(v.car.matrixWorld);
      const C = this.wheel.C.clone().applyMatrix4(toParent);
      const Ax = this.wheel.Ax.clone().transformDirection(toParent);
      return { m, m0: m.matrix.clone(), auto: m.matrixAutoUpdate, C, Ax };
    });
  }

  setSteering(angle) {
    if (!this.steerMeshes) return;
    const T = new THREE.Matrix4(), R = new THREE.Matrix4(), Ti = new THREE.Matrix4();
    for (const s of this.steerMeshes) {
      T.makeTranslation(s.C.x, s.C.y, s.C.z); Ti.makeTranslation(-s.C.x, -s.C.y, -s.C.z);
      R.makeRotationAxis(s.Ax, angle);
      s.m.matrixAutoUpdate = false;
      s.m.matrix.copy(T).multiply(R).multiply(Ti).multiply(s.m0);
    }
  }

  restoreSteering() {
    for (const s of this.steerMeshes || []) { s.m.matrix.copy(s.m0); s.m.matrixAutoUpdate = s.auto; }
  }

  ensureBush() {
    if (this.bush) return;
    const g = new THREE.Group();
    const mat = new THREE.MeshStandardMaterial({ color: 0x3f7a35, roughness: 1 });
    const r = rng(7);
    for (let i = 0; i < 7; i++) {
      const m = new THREE.Mesh(new THREE.IcosahedronGeometry(0.55 + r() * 0.35, 1), mat);
      m.position.set((r() - 0.5) * 2.6, 0.55 + r() * 0.45, (r() - 0.5) * 0.9);
      m.scale.y = 0.85;
      g.add(m);
    }
    g.visible = false;
    this.bush = g;
    this.v.scene.add(g);
  }

  ensureSkid() {
    if (this.skid) { this.skid.count = 0; this.skid.mesh.geometry.setDrawRange(0, 0); this.skid.mesh.visible = true; return; }
    const max = 6000;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(max * 6 * 3), 3));
    geo.setDrawRange(0, 0);
    const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x0b0b0d, transparent: true, opacity: 0.55, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4 }));
    mesh.frustumCulled = false;
    mesh.name = "moose_skidmarks";
    this.v.scene.add(mesh);
    this.skid = { mesh, max, count: 0, last: [] };
  }

  // ---------- запуск ----------
  /** Возвращает Promise: заезд + повтор сзади. */
  play(kind, result, speedKmh) {
    if (!this.active) return Promise.reject(new Error("Тест не открыт"));
    this.finish(true);
    this.cleanupRun();
    this.resetCarPose();
    this.ensureSkid();
    this.dummies.forEach((d) => d.reset());
    const v0 = speedKmh / 3.6;
    const r = kind === "swerve" ? result.swerve : result.collision;
    const accelDist = (v0 * v0) / (2 * 7);
    const plan = this.swervePlan(v0);
    // Лось появляется в последний момент: до начала манёвра остаётся только время реакции (~0,3 с).
    // При ударе — ровно столько, чтобы после реакции успеть сбросить скорость до impact_speed из API.
    const vImp = (result.collision.impact_speed || 0) / 3.6;
    const brakeDist = (v0 * v0 - vImp * vImp) / (2 * BRAKE);
    const trigger = kind === "swerve" ? plan.start + 0.25 * v0 : brakeDist + 0.3 * v0 + 0.45;
    const mooseS = accelDist + 6 + trigger + this.geo.halfLen;
    const seed = Math.round(speedKmh * 1000 + (kind === "hit" ? 7 : 3) + (r.verdict || r.level || "").length * 31);
    this.run = {
      kind, r, result, v0, plan, v: 0, s: 0, lat: 0, psi: 0, roll: 0, pitch: 0, lift: 0, steer: 0,
      aLong: 0, aLat: 0, t: 0, phase: "accel", slow: false, mooseS, trigger, uStart: null,
      moose: { state: "hidden", lat: LEAP_FROM, leapT: 0 }, rand: rng(seed), debris: [], shards: [],
      vImp: (result.collision.impact_speed || 0) / 3.6, damageK: 0, xray: false, sevK: 0,
      events: [],
    };
    const F = this.v.front, Lv = this.v.left, s = ENVIRONMENT.carSpot;
    this.bush.position.set(s.x, 0, s.z).addScaledVector(F, mooseS).addScaledVector(Lv, LEAP_FROM - 0.4);
    this.bush.rotation.y = s.yaw;
    this.bush.visible = true;
    this.elk.visible = false;
    this.clipName = null;
    this.mixer.stopAllAction();
    this.playClip("Stand_Breathing_01");
    this.say(`Разгон до ${speedKmh} км/ч`);
    return new Promise((resolve) => { this.run.resolve = resolve; });
  }

  finish(cancel = false) {
    const resolve = this.run?.resolve;
    if (this.run) this.run.resolve = null;
    if (resolve) resolve(cancel ? "cancelled" : "done");
  }

  cleanupRun() {
    const R = this.run;
    for (const d of R?.debris || []) { d.obj.removeFromParent(); d.src.forEach((m) => (m.visible = true)); }
    if (R?.shardMesh) { R.shardMesh.removeFromParent(); R.shardMesh.geometry.dispose(); }
    for (const e of R?.obstacle?.meshes || []) { e.m.matrix.copy(e.m0); e.m.matrixAutoUpdate = e.auto; }
    this.crumple.restore();
    this.setXray(false);
    this.restoreWindshield();
    if (this.skid) { this.skid.count = 0; this.skid.mesh.geometry.setDrawRange(0, 0); this.skid.last = []; }
    if (this.elk) this.elk.visible = false;
    if (this.bush) this.bush.visible = false;
    this.dummies?.forEach((d) => d.reset());
    this.setSteering(0);
    this.run = null;
  }

  // ---------- кадр ----------
  update(dt) {
    const v = this.v;
    if (this.leaving) { // возврат камеры в экскурсию
      const L = this.leaving;
      L.t += dt;
      const k = ease(L.t / L.dur);
      v.camera.position.lerpVectors(L.fromPos, L.toPos, k);
      v.camera.lookAt(new THREE.Vector3().lerpVectors(L.fromLook, L.toLook, k));
      if (L.t >= L.dur) { this.leaving = null; v.test = null; }
      return;
    }
    // Время сцены = реальное время кадра dt (×1/3 в замедлении), разбитое на равные подшаги.
    // От fps не зависит ни длительность, ни траектория; каждый кадр сдвигает сцену ровно на dt —
    // поэтому и в замедлении движение плавное, без рывков «то 1 шаг, то 2».
    const R = this.run;
    const simDt = R && !R.done ? dt * (R.slow ? 1 / 3 : 1) : dt;
    const n = Math.max(1, Math.ceil(simDt / MAX_STEP)), h = simDt / n;
    for (let i = 0; i < n; i++) {
      this.clock += h;
      if (this.run?.done) this.mixer?.update(h);        // финальный кадр стоит, лось дышит
      else if (this.run) this.stepRun(this.run, h);
    }
    if (this.run && this.crumple.items.length) this.crumple.set(this.run.damageK); // тяжёлое — раз за кадр
    this.loop.update(v.carRoot.position.x);
    this.updateCamera(dt);
    this.updateHud();
  }

  /** Ручной шаг для проверки без requestAnimationFrame (скрытая вкладка). */
  step(dt) { this.update(dt); }

  stepRun(R, dt) {
    R.t += dt;
    const F = this.v.front, Lv = this.v.left, spot = ENVIRONMENT.carSpot;
    const vPrev = R.v, psiPrev = R.psi;
    const front = () => R.s + this.geo.halfLen;
    const gap = () => R.mooseS - front();

    // --- лось: прячется за кустом и выпрыгивает резко ---
    const M = R.moose;
    if (M.state === "hidden" && R.phase !== "accel" && gap() <= R.trigger) {
      M.state = "leap"; M.leapT = 0; R.slow = true; this.say("ЛОСЬ!", false, true);
      this.elk.visible = true; this.playClip("Sprint", { fade: 0.02, timeScale: 2 });
    }
    if (M.state === "leap") {
      M.leapT += dt;
      const k = clamp(M.leapT / LEAP_TIME, 0, 1);
      M.lat = lerp(LEAP_FROM, R.kind === "swerve" ? -0.85 : 0, ease(k)); // при объезде — в правой половине полосы
      M.h = Math.sin(k * Math.PI) * 0.7;
      if (k >= 1) { M.state = "stand"; M.h = 0; this.playClip("Stand_Breathing_01", { fade: 0.2 }); }
    }
    // после проезда машины разворачивается и уходит обратно в кусты (не под камеру)
    if (M.state === "stand" && R.kind === "swerve" && R.s - R.mooseS > 3) { M.state = "run"; this.playClip("Run", { fade: 0.25 }); }
    if (M.state === "run") { M.lat -= 4 * dt; if (M.lat < -5.6) { M.state = "away"; this.playClip("Stand_Breathing_01", { fade: 0.4 }); } }
    const wantYaw = M.state === "run" || M.state === "away" ? Math.PI : 0;  // 0 — мордой влево по дороге
    M.yaw = M.yaw == null ? wantYaw : M.yaw + (wantYaw - M.yaw) * (1 - Math.exp(-dt * 5));

    // --- машина ---
    const sPrev = R.s, latPrev = R.lat;
    if (R.kind === "swerve") this.stepSwerve(R, dt, gap);
    else this.stepHit(R, dt, gap);

    // поза машины
    const v = this.v;
    const placeCar = () => {
      v.carRoot.position.set(spot.x, spot.y, spot.z).addScaledVector(F, R.s).addScaledVector(Lv, R.lat);
      v.carRoot.rotation.set(0, spot.yaw + R.psi, 0);
    };
    placeCar();
    // столбы, деревья, скамейки: машина в них упирается и останавливается
    const hit = R.blocked ? null : R.v > 0.3 ? this.collide(R) : null;
    if (hit) this.hitObstacle(R, hit);
    if (R.blocked) {
      R.s = sPrev; R.lat = latPrev; R.v = 0; R.spin = 0;
      if (R.psiStop == null) R.psiStop = psiPrev;
      R.psi = R.psiStop;
      placeCar();
    }
    if (R.obstacle) this.stepObstacle(R, dt);

    R.aLong = clamp((R.v - vPrev) / Math.max(dt, 1e-4), -60, 30);
    R.aLat = clamp(R.v * (R.psi - psiPrev) / Math.max(dt, 1e-4), -30, 30);
    v.car.rotation.set(-R.pitch, 0, R.roll);
    v.car.position.set(R.roll < 0 ? -this.geo.halfW * (1 - Math.cos(R.roll)) : 0, this.geo.halfW * Math.sin(Math.abs(R.roll)) * R.lift, 0);
    v.carRoot.updateMatrixWorld(true);
    this.setSteering(R.steer);

    // лось в мире
    if (this.elk.visible && M.state !== "fly") {
      const p = new THREE.Vector3(spot.x, 0.02 + (M.h || 0), spot.z).addScaledVector(F, R.mooseS).addScaledVector(Lv, M.lat);
      this.elk.position.copy(p);
      // стоит боком поперёк полосы; M.yaw плавно разворачивает его
      this.elk.quaternion.setFromAxisAngle(UP, Math.atan2(Lv.x, Lv.z) + M.yaw - this.elkFwdAngle);
    }
    this.mixer.update(dt);
    if (this.elk.visible) this.groundElk();

    // манекены
    for (const d of this.dummies) { d.physics(dt, R.aLong, R.aLat); d.applyPose(R.steer); }
    if (R.sevK > 0) this.dummies.forEach((d) => d.colorize(R.result.collision.seats?.[d.name], R.sevK));
    this.stepBodies(R, dt);
    this.addSkid(R);


    if (R.endAt != null && R.t >= R.endAt) { // заезд закончен: финальная картина остаётся на экране
      R.done = true; R.slow = false;
      this.say(R.r.title);
      const resolve = R.resolve; R.resolve = null;
      resolve?.("done");
    }
  }

  /** Манёвр «переставка» по пройденному пути u (м): уход влево на A, удержание, возврат.
   *  Длины растут со скоростью — пиковое боковое ускорение ≈ 0,9 g, как в реальном тесте. */
  /** Пересечение прямоугольника машины (в плане) с кругами препятствий видимых копий квартала. */
  collide(R) {
    const v = this.v, c = v.carRoot.position, L = this.loop;
    const fwd = v.front.clone().applyAxisAngle(UP, R.psi), left = new THREE.Vector3().crossVectors(UP, fwd);
    const hl = this.geo.halfLen, hw = this.geo.halfW;
    for (const u of L.units) {
      if (!u.wrap.visible) continue;
      for (const col of L.colliders) for (const [x, z, r] of col.circles) {
        const wx = L.worldX(u, x), dx = wx - c.x, dz = z - c.z;
        if (dx * dx + dz * dz > 30) continue;
        const df = dx * fwd.x + dz * fwd.z, dl = dx * left.x + dz * left.z;
        if (Math.abs(df) < hl + r && Math.abs(dl) < hw + r) return { u, col, x: wx, z };
      }
    }
    return null;
  }

  hitObstacle(R, hit) {
    R.blocked = true;
    const vHit = R.v;
    const away = new THREE.Vector3(hit.x - this.v.carRoot.position.x, 0, hit.z - this.v.carRoot.position.z).normalize();
    const name = { lamp: "фонарный столб", tree: "дерево", object: "скамейку" }[hit.col.kind];
    this.say(`Удар в ${name} на ${Math.round(vHit * 3.6)} км/ч`, false, true);
    this.cam.shake = Math.min(1, 0.3 + vHit / 15);
    R.slow = false;
    if (R.endAt == null) R.endAt = R.t + 1.4;
    const target = hit.col.kind === "lamp" ? clamp(vHit * 0.05, 0.08, 0.55) : hit.col.kind === "tree" ? 0.05 : 0;
    const meshes = hit.col.tilt.map((i) => hit.u.meshes[i]).filter(Boolean)
      .map((m) => ({ m, m0: m.matrix.clone(), auto: m.matrixAutoUpdate }));
    R.obstacle = { pivot: new THREE.Vector3(hit.x, 0, hit.z), axis: new THREE.Vector3().crossVectors(UP, away).normalize(), target, ang: 0, t: 0, meshes };
  }

  stepObstacle(R, dt) {
    const O = R.obstacle;
    O.t += dt;
    this.setObstacleTilt(O, O.target * ease(O.t / 0.35));
  }

  /** Столб гнётся у основания в сторону от машины (матрицы мешей, исходные сохранены). */
  setObstacleTilt(O, ang) {
    O.ang = ang;
    const W = new THREE.Matrix4().makeTranslation(O.pivot.x, O.pivot.y, O.pivot.z)
      .multiply(new THREE.Matrix4().makeRotationAxis(O.axis, ang))
      .multiply(new THREE.Matrix4().makeTranslation(-O.pivot.x, -O.pivot.y, -O.pivot.z));
    for (const e of O.meshes) {
      const pw = e.m.parent.matrixWorld;
      e.m.matrixAutoUpdate = false;
      e.m.matrix.copy(new THREE.Matrix4().copy(pw).invert().multiply(W).multiply(pw).multiply(e.m0));
      e.m.matrixWorldNeedsUpdate = true;
    }
  }

  swervePlan(v) {
    // Руль — в последний момент: до лося ~0,45 с. Уход резкий (пик ~1,2 g), лось стоит в правой половине
    // полосы, поэтому к моменту, когда нос машины поравняется с ним, смещения 1,6 м уже хватает.
    const A = 3.0, X1 = 1.1 * v, X2 = 1.25 * v, start = 0.42 * X1;
    const H = Math.max(1.5, start + 5 - X1);           // держим полосу, пока корма не прошла лося
    return { A, X1, H, X2, e2: X1 + H, e3: X1 + H + X2, start };
  }

  swervePath(u, P) {
    const { A, X1, X2, e2 } = P;
    if (u <= 0) return 0;
    if (u < X1) return A * (1 - Math.cos(Math.PI * u / X1)) / 2;
    if (u < e2) return A;
    if (u < e2 + X2) return A * (1 + Math.cos(Math.PI * (u - e2) / X2)) / 2;
    return 0;
  }

  stepSwerve(R, dt, gap) {
    const { verdict, mode } = R.r;
    if (R.phase === "accel") {
      R.v = Math.min(R.v0, R.v + 7 * dt); R.s += R.v * dt;
      if (R.v >= R.v0) R.phase = "drive";
      return;
    }
    if (R.phase === "drive") {
      R.s += R.v * dt;
      const P = R.plan;
      if (R.uStart == null && R.moose.state !== "hidden" && gap() <= P.start) { R.uStart = R.s; this.say("Руль влево!"); }
      const u = R.uStart == null ? -1 : R.s - R.uStart;
      const wob = (q) => (verdict === "limit" && q > P.e2 ? 0.45 * Math.sin((q - P.e2) * 0.45) * Math.exp(-(q - P.e2) / (0.6 * P.X2)) : 0);
      const lat = (q) => this.swervePath(q, P) + wob(q);
      R.lat = lat(u);
      const d1 = (lat(u + 0.25) - lat(u - 0.25)) / 0.5;
      const d2 = (lat(u + 0.5) - 2 * lat(u) + lat(u - 0.5)) / 0.25;
      R.psi = Math.atan(d1);
      const latG = (d2 * R.v * R.v) / G;
      const gain = verdict === "pass" ? 0.07 : verdict === "limit" ? 0.17 : 0.14; // рад крена на 1 g
      R.roll = clamp(-gain * latG, -0.3, 0.3);
      R.steer = clamp(d2 * 40, -2.6, 2.6);
      R.skidding = verdict !== "pass" && (Math.abs(latG) > 0.7 || (verdict === "limit" && u > P.e2 && u < P.e3 + 3));
      if (u > P.e2 && !R.saidBack) { R.saidBack = true; this.say("Обратно в полосу"); }
      if (verdict === "fail" && mode === "skid" && u > P.e2 + 0.3 * P.X2) { R.phase = "skid"; R.dir = R.psi; R.spin = -2.2; this.say("Занос!", false, true); }
      if (verdict === "fail" && mode === "rollover" && u > P.e2 + 0.25 * P.X2) { R.phase = "tip"; R.dir = R.psi; R.tipT = 0; this.say("Встаёт на два колеса!", false, true); }
      if (u > P.e2 + 0.5 * P.X2) R.slow = false;
      if (u > P.e3) { R.phase = "stop"; this.say(verdict === "limit" ? "Удержал — на пределе" : "Объехал!"); }
    } else if (R.phase === "stop") {
      R.v = Math.max(0, R.v - 7 * dt); R.s += R.v * dt;
      R.roll *= Math.exp(-dt * 5); R.psi *= Math.exp(-dt * 4); R.steer *= Math.exp(-dt * 4);
      R.skidding = false;
      if (R.v <= 0 && R.endAt == null) R.endAt = R.t + 0.8;
    } else if (R.phase === "skid") { // занос: скользит по инерции и разворачивается
      R.v = Math.max(0, R.v - 6.2 * dt);
      R.s += R.v * Math.cos(R.dir) * dt; R.lat += R.v * Math.sin(R.dir) * dt;
      R.psi += R.spin * dt; R.spin *= Math.exp(-dt * 0.9);
      R.roll = lerp(R.roll, 0.06, 1 - Math.exp(-dt * 3));
      R.steer = 2.2 * Math.sin(R.t * 5) * Math.exp(-(R.v0 - R.v) / R.v0);
      R.skidding = R.v > 1;
      if (R.v <= 0 && R.endAt == null) { R.endAt = R.t + 1.0; R.slow = false; }
    } else if (R.phase === "tip") { // встаёт на два колеса и падает обратно
      R.v = Math.max(0, R.v - 6.5 * dt);
      R.s += R.v * Math.cos(R.dir) * dt; R.lat += R.v * Math.sin(R.dir) * dt;
      R.tipT += dt;
      const up = smooth(R.tipT / 0.55), down = smooth((R.tipT - 1.1) / 0.45);
      R.roll = -0.62 * up * (1 - down) + (R.tipT > 1.55 && R.tipT < 1.95 ? 0.05 * Math.sin((R.tipT - 1.55) * 16) : 0);
      R.lift = 1;
      R.skidding = R.v > 1;
      R.skidOnly = "right";
      if (R.tipT > 2.3 && R.endAt == null) { R.endAt = R.t + 0.8; R.slow = false; }
    }
  }

  stepHit(R, dt, gap) {
    const col = R.r;
    if (R.phase === "accel") {
      R.v = Math.min(R.v0, R.v + 7 * dt); R.s += R.v * dt;
      if (R.v >= R.v0) R.phase = "drive";
      return;
    }
    const contact = () => gap() - 0.45; // до бока лося
    if (R.phase === "drive") {
      R.s += R.v * dt;
      const brakeDist = (R.v0 * R.v0 - R.vImp * R.vImp) / (2 * BRAKE);
      if (R.moose.state !== "hidden" && contact() <= brakeDist) { R.phase = "brake"; this.say("Тормоз!"); }
    } else if (R.phase === "brake") {
      R.v = Math.max(R.vImp, R.v - BRAKE * dt); R.s += R.v * dt;
      R.pitch = Math.min(0.035, R.pitch + dt * 0.25);
      R.skidding = true;
      if (contact() <= 4) R.slow = true;
      if (contact() <= 0) this.impact(R);
    } else if (R.phase === "crash") {
      const k = R.t - R.impactT;
      R.v = Math.max(0, R.vAt * (1 - k / 0.45)); R.s += R.v * dt;
      R.pitch = 0.035 + 0.075 * Math.sin(clamp(k / 0.5, 0, 1) * Math.PI);
      R.skidding = R.v > 1;
      R.damageK = ease(k / 0.12);
      for (const d of this.dummies) if (d.bag) {
        d.bag.visible = true;
        const b = k < 0.03 ? ease(k / 0.03) : 1 - 0.3 * smooth((k - 1.2) / 0.8);
        d.bag.scale.set(b, b * 0.9, b * 0.75);
        d.limit = 0.42;                                  // подушка останавливает корпус раньше ремня
      }
      this.stepElkHit(R, k, dt);
      if (k > 0.9) R.slow = false;
      if (k > 0.5) R.sevK = smooth((k - 0.5) / 0.6);
      if (R.v <= 0 && R.endAt == null) { R.endAt = R.t + 1.2; this.say(col.title); }
    }
  }

  impact(R) {
    const col = R.r, v = this.v;
    R.phase = "crash"; R.impactT = R.t; R.vAt = R.v;
    this.cam.shake = 1;
    this.say(`Удар на ${Math.round(R.v * 3.6)} км/ч`, false, true);
    R.xray = true; this.setXray(true);
    const sev = { low: 0.35, medium: 0.6, high: 0.85, critical: 1 }[col.level] || 0.6;
    const speedK = clamp(R.vAt / 14, 0.4, 1.4);
    const g = this.geo;
    const meshes = ["hood", "front_bumper", "grille", "headlight_left", "headlight_right", "body_paint", "windshield", "roof"]
      .flatMap((id) => v.partMeshes.get(id) || []);
    const ws = g.windshield;
    this.crumple.prepare(meshes, v.car, g.box.min.z, g.box.max.y - 0.3, {
      crush: (col.roof_hit ? 0.22 : 0.38) * sev * speedK,
      dent: col.roof_hit ? 0.28 * sev * speedK : 0,
      dentZ0: ws ? ws.min.z - 0.1 : g.box.min.z + 1.2, dentZ1: ws ? ws.max.z + 0.9 : g.box.min.z + 2.6,
    }, R.rand);
    this.breakWindshield(R, sev);
    this.detachParts(R, col.level);
    // лось
    const M = R.moose;
    M.state = "fly"; M.flyT = 0;
    M.p0 = this.elk.position.clone();
    this.playClip("Hit_Stand_L01", { once: true, fade: 0.05 });
  }

  stepElkHit(R, k, dt) {
    const F = this.v.front, Lv = this.v.left;
    const qYaw = new THREE.Quaternion().setFromAxisAngle(UP, Math.atan2(Lv.x, Lv.z) - this.elkFwdAngle); // как стоял
    const frontPt = this.v.carRoot.position.clone().addScaledVector(F, this.geo.halfLen);
    const M = R.moose;
    let pos, beta;
    if (R.r.roof_hit) {
      // низкий капот подбивает ноги: туша опрокидывается назад на капот и лобовое,
      // а когда машина встала — сползает с капота вперёд на асфальт и ложится перед бампером
      const a = ease(k / 0.35);
      const onCar = frontPt.clone().addScaledVector(F, -0.15).add(new THREE.Vector3(0, 0.82, 0));
      pos = new THREE.Vector3().lerpVectors(M.p0, onCar, a);
      beta = -1.25 * a;
      if (R.v <= 0.05) {
        M.slideT = (M.slideT || 0) + dt;
        const s = ease(M.slideT / 1.0);
        pos.addScaledVector(F, 1.6 * s);
        pos.y = lerp(pos.y, 0, s);
        beta = lerp(-1.25, 0, s);
        if (M.slideT > 0.25) this.playClip("Lying_Breathing_01", { fade: 0.6 });
      }
    } else {
      // высокий капот: удар принимают бампер и капот, лося отбрасывает вперёд по ходу; приземлился — лежит
      const t = Math.min(k, 1.4);
      pos = M.p0.clone().addScaledVector(F, R.vAt * 0.75 * Math.min(t, 0.7) + 2.5 * Math.max(0, t - 0.7));
      const y = 0.02 + 3.0 * k - 0.5 * G * k * k;
      pos.y = Math.max(0, y);
      if (y > 0 || k < 0.2) beta = 1.3 * ease(k / 0.6);
      else {
        M.landT = (M.landT || 0) + dt;
        beta = 1.3 * (1 - ease(M.landT / 0.5));
        this.playClip("Lying_Breathing_01", { fade: 0.45 });
      }
    }
    this.elk.position.copy(pos);
    const qFall = new THREE.Quaternion().setFromAxisAngle(Lv, beta);
    this.elk.quaternion.copy(qFall).multiply(qYaw);
  }

  /** Самая низкая кость скелета не ниже асфальта (клипы лёжа/падения иначе уводят тушу под землю). */
  groundElk(minY = 0.06) {
    this.elk.updateMatrixWorld(true);
    let low = Infinity;
    const p = new THREE.Vector3();
    for (const b of this.elkBones) low = Math.min(low, p.setFromMatrixPosition(b.matrixWorld).y);
    if (low < minY) { this.elk.position.y += minY - low; this.elk.updateMatrixWorld(true); }
  }

  breakWindshield(R, sev) {
    const v = this.v;
    this.windshieldMats = [];
    for (const m of v.partMeshes.get("windshield") || []) {
      const mats = materialsOf(m).map((mat) => { this.windshieldMats.push({ mat, color: mat.color.getHex(), opacity: mat.opacity }); return mat; });
      for (const mat of mats) { mat.color.setHex(0xdfe7ee); mat.opacity = 0.55 + 0.25 * sev; }
    }
    // осколки — мелкие треугольники с гравитацией и отскоком
    const wb = v.boxOfPart("windshield");
    if (!wb) return;
    const n = Math.round(40 + 60 * sev);
    const tri = new THREE.BufferGeometry();
    tri.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 0.07, 0.01, 0, 0.02, 0.06, 0.01], 3));
    tri.computeVertexNormals();
    const mesh = new THREE.InstancedMesh(tri, new THREE.MeshStandardMaterial({ color: 0xcfe3ef, roughness: 0.1, metalness: 0.2, side: THREE.DoubleSide, transparent: true, opacity: 0.85 }), n);
    mesh.name = "moose_glass";
    mesh.frustumCulled = false;
    const F = v.front, Lv = v.left, rand = R.rand;
    R.shards = [];
    for (let i = 0; i < n; i++) {
      const p = new THREE.Vector3(lerp(wb.min.x, wb.max.x, rand()), lerp(wb.min.y, wb.max.y, rand()), lerp(wb.min.z, wb.max.z, rand()));
      const vel = new THREE.Vector3().addScaledVector(F, R.vAt * (0.3 + rand() * 0.5)).addScaledVector(Lv, (rand() - 0.5) * 3).add(new THREE.Vector3(0, 1 + rand() * 2.5, 0));
      R.shards.push({ p, vel, q: new THREE.Quaternion().setFromEuler(new THREE.Euler(rand() * 6, rand() * 6, rand() * 6)), w: new THREE.Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5).multiplyScalar(20), rest: false });
    }
    this.v.scene.add(mesh);
    R.shardMesh = mesh;
  }

  restoreWindshield() {
    for (const w of this.windshieldMats || []) { w.mat.color.setHex(w.color); w.mat.opacity = w.opacity; }
    this.windshieldMats = [];
  }

  detachParts(R, level) {
    const v = this.v;
    const ids = { low: [], medium: ["grille"], high: ["grille", "front_bumper"], critical: ["grille", "front_bumper", "mirror_right", "headlight_right"] }[level] || [];
    const F = v.front, Lv = v.left, rand = R.rand;
    for (const id of ids) {
      const src = (v.partMeshes.get(id) || []).filter((m) => m.visible);
      if (!src.length) continue;
      const obj = new THREE.Group();
      obj.name = `moose_debris_${id}`;
      const box = new THREE.Box3();
      src.forEach((m) => box.expandByObject(m));
      const c = box.getCenter(new THREE.Vector3());
      obj.position.copy(c);
      for (const m of src) {
        const copy = new THREE.Mesh(m.geometry, m.material);
        m.updateMatrixWorld(true);
        copy.matrixAutoUpdate = false;
        copy.matrix.copy(new THREE.Matrix4().makeTranslation(-c.x, -c.y, -c.z).multiply(m.matrixWorld));
        obj.add(copy);
        m.visible = false;
      }
      v.scene.add(obj);
      const half = Math.min(...box.getSize(new THREE.Vector3()).toArray()) / 2 + 0.03;
      R.debris.push({
        obj, src, half,
        vel: new THREE.Vector3().addScaledVector(F, R.vAt * (0.55 + rand() * 0.4)).addScaledVector(Lv, (rand() - 0.5) * 4).add(new THREE.Vector3(0, 2 + rand() * 2.5, 0)),
        w: new THREE.Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5).multiplyScalar(9),
      });
    }
  }

  stepBodies(R, dt) {
    const tmpQ = new THREE.Quaternion(), e = new THREE.Euler();
    const integrate = (b, pos, quat, half) => {
      if (b.rest) return;
      b.vel.y -= G * dt;
      pos.addScaledVector(b.vel, dt);
      e.set(b.w.x * dt, b.w.y * dt, b.w.z * dt);
      quat.multiply(tmpQ.setFromEuler(e));
      if (pos.y < half) { // земля: отскок и трение
        pos.y = half;
        b.vel.y = -b.vel.y * 0.3;
        b.vel.x *= 0.72; b.vel.z *= 0.72; b.w.multiplyScalar(0.6);
        if (Math.abs(b.vel.y) < 0.4 && Math.hypot(b.vel.x, b.vel.z) < 0.3) b.rest = true;
      }
    };
    for (const d of R.debris) integrate(d, d.obj.position, d.obj.quaternion, d.half);
    if (R.shardMesh) {
      const M = new THREE.Matrix4(), one = new THREE.Vector3(1, 1, 1);
      R.shards.forEach((s, i) => { integrate(s, s.p, s.q, 0.025); R.shardMesh.setMatrixAt(i, M.compose(s.p, s.q, one)); });
      R.shardMesh.instanceMatrix.needsUpdate = true;
    }
  }

  addSkid(R) {
    const S = this.skid;
    if (!R.skidding || R.v < 0.5) { S.last = []; return; }
    const car = this.v.car;
    const pts = this.geo.wheels.map((w, i) => ({ i, p: w.clone().applyMatrix4(car.matrixWorld).setY(0.028), right: w.x > 0 }));
    const arr = S.mesh.geometry.attributes.position.array;
    for (const { i, p, right } of pts) {
      if (R.skidOnly === "right" && !right) { S.last[i] = null; continue; }
      const prev = S.last[i];
      S.last[i] = p;
      if (!prev || prev.distanceTo(p) < 0.15 || S.count >= S.max) continue;
      const d = p.clone().sub(prev).setY(0).normalize();
      const n = new THREE.Vector3(-d.z, 0, d.x).multiplyScalar(0.09);
      const a1 = prev.clone().add(n), a2 = prev.clone().sub(n), b1 = p.clone().add(n), b2 = p.clone().sub(n);
      const o = S.count * 18;
      [a1, a2, b1, a2, b2, b1].forEach((q, j) => { arr[o + j * 3] = q.x; arr[o + j * 3 + 1] = q.y; arr[o + j * 3 + 2] = q.z; });
      S.count++;
    }
    S.mesh.geometry.attributes.position.needsUpdate = true;
    S.mesh.geometry.setDrawRange(0, S.count * 6);
  }

  /** Рентген: внешние панели полупрозрачные — видно манекены в салоне. */
  setXray(on) {
    const v = this.v;
    if (on === !!this.xrayMats) return;
    if (on) {
      this.xrayMats = [];
      for (const [id, part] of v.parts) {
        if (part.group !== "exterior" || /wheel|light|glass|window|windshield/.test(id)) continue;
        for (const m of v.partMeshes.get(id) || []) for (const mat of materialsOf(m)) {
          this.xrayMats.push({ mat, t: mat.transparent, o: mat.opacity, d: mat.depthWrite });
          Object.assign(mat, { transparent: true, opacity: 0.22, depthWrite: false });
          mat.needsUpdate = true;
        }
      }
    } else {
      for (const x of this.xrayMats) { Object.assign(x.mat, { transparent: x.t, opacity: x.o, depthWrite: x.d }); x.mat.needsUpdate = true; }
      this.xrayMats = null;
    }
  }

  // ---------- камера ----------
  orbitBy(angle) {
    if (this.leaving) return;
    this.orbitOffset ??= this.v.camera.position.clone().sub(this.v.carRoot.position);
    this.orbitOffset.applyAxisAngle(UP, angle);
  }

  desiredCamera(mode) {
    const v = this.v, c = v.carRoot.position.clone(), F = v.front, Lv = v.left;
    const at = (f, l, u) => c.clone().addScaledVector(F, f).addScaledVector(Lv, l).add(new THREE.Vector3(0, u, 0));
    switch (mode) {
      case "wide": return { pos: at(-9, 2.6, 4.2), look: at(11, -0.5, 0.6) };
      case "rear": return { pos: at(-6.2, 0, 1.55), look: at(6, 0, 0.9) };
      case "side": return { pos: at(0.3, 4.1, 1.45), look: at(0.2, 0, 0.8) }; // со встречной полосы, не в кронах деревьев
      case "top": return { pos: at(-3, 0, 15), look: at(3, 0, 0) };
      default: return { pos: at(-7.2, 1.5, 2.6), look: at(5, 0, 0.8) };
    }
  }

  updateCamera(dt) {
    const v = this.v, R = this.run;
    const mode = R?.kind === "hit" && (R.phase === "crash" || R.done) ? "side" : "chase";
    const want = this.desiredCamera(mode);
    if (this.orbitOffset) {
      want.pos.copy(v.carRoot.position).add(this.orbitOffset);
      want.look.copy(v.carRoot.position); want.look.y += 0.75;
    }
    want.pos.z = clamp(want.pos.z, -1.5, 8.5);
    const k = 1 - Math.exp(-dt * (this.orbitOffset ? 16 : 6));
    if (this.fly) { // плавный перелёт из экскурсии
      this.fly.t += dt;
      const f = ease(this.fly.t / this.fly.dur);
      this.cam.pos.lerpVectors(this.fly.fromPos, want.pos, f);
      this.cam.look.lerpVectors(this.fly.fromLook, want.look, f);
      if (this.fly.t >= this.fly.dur) this.fly = null;
    } else {
      this.cam.pos.lerp(want.pos, k);
      this.cam.look.lerp(want.look, k);
    }
    const p = this.cam.pos.clone();
    if (this.cam.shake > 0.01) {
      const c = this.clock * 1000; // время сцены, а не стенные часы
      p.add(new THREE.Vector3(Math.sin(c * 0.09), Math.sin(c * 0.13), Math.cos(c * 0.11)).multiplyScalar(0.18 * this.cam.shake));
      this.cam.shake *= Math.exp(-dt * 3.5);
    }
    v.camera.position.copy(p);
    v.camera.lookAt(this.cam.look);
    // горизонтальный угол обзора ~70°; низ экрана занят панелью — картинку сдвигаем вверх
    const w = v.container.clientWidth, h = v.container.clientHeight;
    if (w && h) {
      const inset = Math.min(this.bottomInset, h * 0.6);
      const aspect = w / (h + inset);
      const vfov = Math.min(80, THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(mode === "top" ? 60 : 72) / 2) / aspect)));
      v.camera.aspect = aspect;
      v.camera.fov = vfov;
      if (inset > 0) v.camera.setViewOffset(w, h + inset, 0, inset, w, h); else v.camera.clearViewOffset();
      v.camera.updateProjectionMatrix();
    }
  }

  setBottomInset(px) {
    this.bottomInset = Math.max(0, px || 0);
    if (!px) {
      const v = this.v, w = v.container.clientWidth, h = v.container.clientHeight;
      v.camera.clearViewOffset();
      if (w && h) { v.camera.aspect = w / h; v.camera.updateProjectionMatrix(); }
    }
  }

  // ---------- HUD ----------
  makeHud() {
    const root = document.createElement("div");
    root.className = "mt-hud";
    root.innerHTML = '<div class="mt-caption"></div><div class="mt-readout"><b class="mt-speed">0</b><span>км/ч</span><i class="mt-roll"></i></div><div class="mt-slow" hidden>Замедленно ×3</div>';
    this.overlay.appendChild(root);
    this.hud = { root, caption: root.querySelector(".mt-caption"), speed: root.querySelector(".mt-speed"), roll: root.querySelector(".mt-roll"), slow: root.querySelector(".mt-slow") };
  }

  say(text, calm = false, alarm = false) {
    const c = this.hud.caption;
    c.textContent = text;
    c.classList.toggle("alarm", alarm);
    c.classList.toggle("calm", calm);
    c.classList.remove("pop"); void c.offsetWidth; c.classList.add("pop");
  }

  updateHud() {
    const R = this.run;
    this.hud.root.hidden = false;
    const speed = R ? R.v * 3.6 : 0;
    this.hud.speed.textContent = speed.toFixed(1).replace(".", ",");
    const deg = Math.abs((R?.roll || 0) * 180 / Math.PI);
    this.hud.roll.textContent = `крен ${deg.toFixed(0)}°`;
    this.hud.roll.className = `mt-roll ${deg > 25 ? "bad" : deg > 12 ? "warn" : ""}`;
    this.hud.slow.hidden = !(R?.slow && !R.done);
  }
}
