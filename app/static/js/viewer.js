// 3D-сцена на весь экран: машина стоит на улице города, прокрутка ведёт камеру
// по «экскурсии» (общий вид → перёд → колёса → бок → зад → салон → управление),
// у деталей появляются сноски с линиями. Перетаскивание — осмотреться вокруг.
//
// Детали описаны в parts.json рядом с моделью (id, label_ru, group, nodes, anchor).
// Текст сноски = название детали + факт об этой машине из БД (cars.hotspots[id]).
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";

// Окружение: улица из game-ready-city-buildings-pack (Draco + WebP, ~3 МБ).
// Пак сделан в масштабе 1:5 — увеличиваем до метров. Улица идёт вдоль оси X,
// проезжая часть z ∈ [0.5; 6.5], тротуары до z = -3 и z = 10, дальше дома.
// Справа асфальт и тротуары обрываются на x ≈ 18.0 (дома торчат дальше, до 20.3) —
// там стыкуем зеркальное отражение улицы (относительно плоскости x = 18). Именно отражение,
// а не разворот на 180°: разметка в паке несимметрична, и при развороте линии съезжают.
export const ENVIRONMENT = {
  url: "/static/media/env/street.glb",
  scale: 5,
  mirrorAt: { x: 17.98 },                                  // плоскость отражения: край асфальта и разметки
  carSpot: { x: 8, y: 0.02, z: 5.0, yaw: -Math.PI / 2 },  // правая полоса, носом вдоль улицы (+X)
  corridor: { zMin: -1.5, zMax: 8.5 },                    // камера не уходит за тротуары в дома
  markings: "roadmarkings001",  // полоса разметки короче асфальта — достраиваем её до стыка
  treePattern: /^tree|^2023manual/i, // стволы и кроны: становятся прозрачными, если закрывают машину
  // «задники» за рядами домов: в просветах между зданиями видно фасад, а не пустоту
  backdrops: [
    { z: 24.6, facing: -1 },  // за южным рядом, смотрит на улицу
    { z: -22.6, facing: 1 },  // за северным рядом
  ],
};
const DRACO_PATH = "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/libs/draco/gltf/";
const SKY = 0xc9d8e6;

const COLORS = {
  red: 0xb71c1c, white: 0xf2f2f2, black: 0x1b1b1b, blue: 0x1e4fb8, gray: 0x7b8088,
  green: 0x2e6b32, silver: 0xc0c4ca, yellow: 0xf6c026, orange: 0xe8700a, brown: 0x6d4c41,
};
const PAINT_MATERIALS = ["kuzov", "paint"];
const GLASS_MATERIALS = ["steklo", "stekloton", "glass"];
const matName = (m) => (m?.name || "").toLowerCase();
const materialsOf = (o) => (Array.isArray(o.material) ? o.material : [o.material]);
const isGlass = (o) => materialsOf(o).every((m) => GLASS_MATERIALS.includes(matName(m)));
const ease = (t) => 1 - Math.pow(1 - t, 3);
const smooth = (t) => t * t * (3 - 2 * t);
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const UP = new THREE.Vector3(0, 1, 0);
const HIGHLIGHT_MATERIAL = new THREE.MeshBasicMaterial({
  color: 0xff4438, transparent: true, opacity: 0.5, depthWrite: false,
  side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
});

// Экскурсия. pose(v) возвращает {pos, target}; hfov — горизонтальный угол обзора.
// Позиции считаются от машины (перёд/лево/верх), поэтому работают для любой модели и места.
export const TOUR = [
  // улица узкая (до домов ~6 м), поэтому камера снаружи держится вдоль дороги
  { id: "overview", title: "Общий вид", parts: [], hfov: 70,
    pose: (v) => v.ext(6.2, 2.4, 2.2, v.ground(0.6)) },
  { id: "front", title: "Спереди", parts: ["hood", "headlight_left", "front_bumper"], hfov: 62,
    pose: (v) => v.ext(4.6, 1.0, 1.4, v.partCenter("hood")) },
  { id: "wheels", title: "Колёса и привод", parts: ["wheel_front_left", "mirror_left", "door_front_left"], hfov: 78,
    pose: (v) => v.ext(0.4, 3.4, 0.8, v.ground(0.4)) },
  { id: "side", title: "Кузов и места", parts: ["door_rear_left", "body_paint", "roof"], hfov: 80,
    pose: (v) => v.ext(-0.6, 3.8, 1.7, v.ground(0.8)) },
  { id: "rear", title: "Сзади", parts: ["trunk_lid", "taillight_left", "rear_bumper"], hfov: 62,
    pose: (v) => v.ext(-5.0, 1.6, 1.8, v.partCenter("trunk_lid")) },
  { id: "cabin", title: "Место водителя", parts: ["steering_wheel", "instrument_cluster", "multimedia_screen", "air_vents"],
    hfov: 100, interior: true, pose: (v) => v.cabin(v.lookAhead(-0.12, -0.32)) },
  { id: "console", title: "Управление", parts: ["gear_shifter", "handbrake", "center_console", "pedals"],
    hfov: 100, interior: true, pose: (v) => v.cabin(v.partCenter("gear_shifter")) },
];

const ALL_TOUR = [...TOUR];

// Зачем нужна деталь — показывается, когда реальных цифр об этой машине для детали нет (ничего не выдумываем)
const PART_ABOUT = [
  [/^hood$/, "Закрывает моторный отсек: доступ к двигателю и жидкостям"],
  [/^body_paint$/, "Несущий кузов: держит все узлы и принимает удар при аварии"],
  [/^roof$/, "Крыша — элемент жёсткости кузова, защищает при опрокидывании"],
  [/^trunk_lid$/, "Крышка багажного отсека"],
  [/^front_bumper$/, "Поглощает энергию лёгких ударов и защищает пешехода"],
  [/^rear_bumper$/, "Защищает багажник и фонари при парковке"],
  [/^grille$/, "Пропускает воздух к радиатору для охлаждения двигателя"],
  [/^door_(front|rear)/, "Дверь с усилителем от бокового удара"],
  [/^fender_front/, "Переднее крыло закрывает колесо и защищает от грязи"],
  [/^fender_rear/, "Заднее крыло — часть силового кузова, рядом горловина бака"],
  [/^sill_/, "Порог — силовой элемент кузова под дверями"],
  [/^pillar_a/, "Стойка A держит крышу и лобовое стекло, важна при опрокидывании"],
  [/^pillar_c/, "Стойка C держит крышу над задними пассажирами"],
  [/^mirror_/, "Боковое зеркало: обзор соседних полос"],
  [/^headlight_/, "Освещает дорогу ночью"],
  [/^fog_light_/, "Противотуманная фара: светит низко под туманом"],
  [/^taillight_/, "Габарит, стоп-сигнал и поворотник для машин сзади"],
  [/^handle_/, "Ручка открывания двери"],
  [/^plate_/, "Место под государственный номер"],
  [/^badge_/, "Эмблема марки"],
  [/^wipers$/, "Очищают лобовое стекло от дождя и снега"],
  [/^exhaust$/, "Выводит выхлопные газы из двигателя"],
  [/^(wheel|tire)_/, "Шина — единственный контакт машины с дорогой"],
  [/^rim_/, "Колёсный диск держит шину и передаёт усилие от ступицы"],
  [/^brake_/, "Тормозной механизм колеса"],
  [/^steering_wheel$/, "Руль: управление направлением движения"],
  [/^dashboard$/, "Передняя панель: воздуховоды, органы управления, подушка пассажира"],
  [/^instrument_cluster$/, "Приборы: скорость, обороты, топливо, предупреждения"],
  [/^multimedia_screen$/, "Экран мультимедиа: музыка, навигация, настройки"],
  [/^gear_shifter$/, "Выбор передачи или режима коробки"],
  [/^handbrake$/, "Стояночный тормоз удерживает машину на месте"],
  [/^pedals$/, "Педали газа и тормоза (и сцепления на механике)"],
  [/^center_console$/, "Центральная консоль: подлокотник, ниши, кнопки"],
  [/^seat_(driver|passenger)$/, "Переднее сиденье с подголовником и ремнём"],
  [/^seat_rear$/, "Задний диван для пассажиров"],
  [/^door_panels$/, "Обивка дверей: подлокотники, кнопки стёкол, динамики"],
  [/^floor_carpet$/, "Пол салона с ковровым покрытием и шумоизоляцией"],
  [/^headliner$/, "Обивка потолка: свет в салоне, ручки, шумоизоляция"],
];
const aboutPart = (id) => PART_ABOUT.find(([rx]) => rx.test(id))?.[1] ?? "";

const gltfLoader = new GLTFLoader().setDRACOLoader(new DRACOLoader().setDecoderPath(DRACO_PATH));

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

export class CarViewer {
  /** container — куда положить canvas; overlay — {callouts, lines} для сносок */
  constructor(container, overlay) {
    this.container = container;
    this.callouts = overlay.callouts;
    this.lines = overlay.lines;
    this.hotspots = {};
    this.parts = new Map();
    this.partMeshes = new Map();
    this.cache = new Map();
    this.hoveredPart = null;
    this.p = 0; this.targetP = 0;            // прогресс экскурсии (0..TOUR.length-1)
    this.yaw = 0; this.pitch = 0;            // осмотр перетаскиванием
    this.frame = 0;
    this.test = null;                        // активный тест на лося (moose3d.js): он ведёт машину и камеру
    this.occluded = new Map();               // id детали -> закрыта ли она сейчас

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.localClippingEnabled = true; // тест на лося режет копии квартала плоскостями
    this.renderer.domElement.style.touchAction = "pan-y"; // вертикальный свайп = прокрутка страницы
    container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(SKY);
    this.scene.fog = new THREE.Fog(SKY, 22, 65); // дальние концы улицы растворяются в дымке
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.02, 400);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x5a5a66, 1.7));
    const sun = new THREE.DirectionalLight(0xfff4e0, 2.0);
    sun.position.set(20, 40, 10);
    this.scene.add(sun);

    this.carRoot = new THREE.Group(); // ставит машину в точку на улице
    const s = ENVIRONMENT.carSpot;
    this.carRoot.position.set(s.x, s.y, s.z);
    this.carRoot.rotation.y = s.yaw;
    this.carRoot.add(this.contactShadow());
    this.scene.add(this.carRoot);

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.bindPointer();
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();

    this.envReady = this.loadEnvironment();
    this.renderer.setAnimationLoop(() => this.tick());
  }

  // ---------- загрузка ----------
  async loadEnvironment() {
    // земля под всем: через просветы между домами видно её, а не пустоту
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(1200, 1200),
      new THREE.MeshStandardMaterial({ color: 0x55585e, roughness: 1 }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.03;
    this.scene.add(ground);
    try {
      const E = ENVIRONMENT;
      const street = (await gltfLoader.loadAsync(E.url)).scene;
      street.scale.setScalar(E.scale);
      street.updateMatrixWorld(true);
      street.position.y = -new THREE.Box3().setFromObject(street).min.y; // асфальт на y≈0
      street.traverse((o) => {
        if (!o.isMesh) return;
        for (const m of materialsOf(o)) m.side = THREE.DoubleSide; // у дороги в паке нормали смотрят вниз
      });
      const env = new THREE.Group();
      env.add(street);
      env.updateMatrixWorld(true);
      if (E.markings) this.extendMarkings(street, E.markings); // до копии — чтобы копия получила то же
      if (E.mirrorAt) { // продолжение улицы: зеркальное отражение относительно плоскости x = mirrorAt.x
        const copy = street.clone();
        const pivot = new THREE.Group();
        pivot.position.x = E.mirrorAt.x;
        pivot.scale.x = -1; // three.js сам переворачивает порядок граней при отрицательном масштабе
        copy.position.x -= E.mirrorAt.x;
        pivot.add(copy);
        env.add(pivot);
      }
      for (const b of E.backdrops || []) env.add(this.backdrop(b));
      env.updateMatrixWorld(true);
      env.traverse((o) => { o.matrixAutoUpdate = false; });
      this.collectTrees(env, E.treePattern);
      this.scene.add(env);
      this.environment = env; // вся улица экскурсии (тест на лося прячет её и строит свою ленту)
      this.street = street;   // исходный квартал — из него тест собирает зацикленную улицу
    } catch (e) {
      console.warn("environment failed, plain ground only", e);
    }
  }

  // Разметка — полоса из одинаковых плиток с нарисованными линиями. Она короче асфальта на одну
  // плитку: копируем последнюю плитку и сдвигаем на её длину — пунктир продолжается в том же ритме.
  extendMarkings(street, name) {
    let mark = null;
    street.traverse((o) => { if (o.isMesh && o.name === name) mark = o; });
    if (!mark?.geometry.index) return;
    const pos = mark.geometry.attributes.position, idx = mark.geometry.index;
    const world = (i) => new THREE.Vector3().fromBufferAttribute(pos, i).applyMatrix4(mark.matrixWorld);
    const xs = [...new Set(Array.from({ length: pos.count }, (_, i) => +world(i).x.toFixed(2)))].sort((a, b) => a - b);
    if (xs.length < 3) return;
    const end = xs[xs.length - 1], tile = end - xs[xs.length - 2]; // длина плитки = последний шаг по x
    const keep = [];
    for (let t = 0; t < idx.count; t += 3) {
      const cx = (world(idx.getX(t)).x + world(idx.getX(t + 1)).x + world(idx.getX(t + 2)).x) / 3;
      if (cx > end - tile) keep.push(idx.getX(t), idx.getX(t + 1), idx.getX(t + 2));
    }
    const geo = mark.geometry.clone();
    geo.setIndex(keep);
    const ext = new THREE.Mesh(geo, mark.material);
    ext.name = `${name}_ext`;
    ext.position.copy(mark.position);
    ext.quaternion.copy(mark.quaternion);
    ext.scale.copy(mark.scale);
    // сдвиг на одну плитку вдоль мировой X, пересчитанный в координаты родителя
    const parent = mark.parent;
    const a = parent.worldToLocal(new THREE.Vector3(0, 0, 0)), b = parent.worldToLocal(new THREE.Vector3(tile, 0, 0));
    ext.position.add(b.sub(a));
    parent.add(ext);
  }

  // ---------- деревья, которые закрывают машину, становятся полупрозрачными ----------
  collectTrees(env, pattern) {
    this.trees = [];
    if (!pattern) return;
    env.traverse((o) => {
      if (!o.isMesh || !pattern.test(o.name)) return;
      // свой материал у каждого дерева (копия улицы делит материалы с оригиналом)
      const mats = materialsOf(o).map((m) => {
        const c = m.clone();
        c.userData.baseOpacity = m.opacity;
        c.userData.baseTransparent = m.transparent;
        return c;
      });
      o.material = mats.length === 1 ? mats[0] : mats;
      const c = new THREE.Box3().setFromObject(o).getCenter(new THREE.Vector3());
      this.trees.push({ mesh: o, mats, cur: 1, target: 1, c });
    });
    // крона и ствол — разные меши; объединяем всё, что стоит в одной точке, в одно дерево
    for (const t of this.trees) {
      t.group = this.trees.filter((u) => Math.hypot(u.c.x - t.c.x, u.c.z - t.c.z) < 1.5);
    }
  }

  updateTreeFade() {
    if (!this.trees?.length || !this.car) return;
    if (this.frame % 3 === 0) {
      for (const t of this.trees) t.target = 1;
      if (!this.interior) {
        // лучи из камеры к нескольким точкам машины: центр, перёд, зад, крыша
        const c = this.center, h = (this.box.max.y - this.box.min.y) / 2;
        const len = Math.abs((this.box.max.x - this.box.min.x) * this.front.x) + Math.abs((this.box.max.z - this.box.min.z) * this.front.z);
        const points = [c, c.clone().addScaledVector(this.front, len * 0.4), c.clone().addScaledVector(this.front, -len * 0.4),
          c.clone().add(new THREE.Vector3(0, h * 0.8, 0)), c.clone().add(new THREE.Vector3(0, -h * 0.5, 0))];
        const meshes = this.trees.map((t) => t.mesh);
        for (const p of points) {
          const dir = p.clone().sub(this.camera.position);
          const dist = dir.length();
          this.raycaster.set(this.camera.position, dir.normalize());
          this.raycaster.far = dist;
          for (const hit of this.raycaster.intersectObjects(meshes, false)) {
            for (const u of this.trees.find((t) => t.mesh === hit.object).group) u.target = 0.22;
          }
        }
        this.raycaster.far = Infinity;
      }
    }
    for (const t of this.trees) {
      if (Math.abs(t.cur - t.target) < 0.01) { if (t.cur === t.target) continue; t.cur = t.target; }
      else t.cur += (t.target - t.cur) * 0.15;
      const faded = t.cur < 0.99;
      for (const m of t.mats) {
        m.opacity = m.userData.baseOpacity * t.cur;
        const transparent = faded || m.userData.baseTransparent;
        if (m.transparent !== transparent) { m.transparent = transparent; m.needsUpdate = true; }
        m.depthWrite = !faded;
      }
    }
  }

  // Стена-фасад с окнами (текстура рисуется на canvas): дешёвая замена ещё одного ряда домов
  backdrop({ z, facing }) {
    if (!this.facadeTexture) {
      const c = document.createElement("canvas");
      c.width = 256; c.height = 256;
      const g = c.getContext("2d");
      g.fillStyle = "#8f877c"; g.fillRect(0, 0, 256, 256);           // стена в тон фасадов пака
      g.fillStyle = "#7b746a"; g.fillRect(0, 0, 256, 18);            // межэтажный пояс
      for (let x = 0; x < 4; x++) for (let y = 0; y < 2; y++) {
        g.fillStyle = (x + y) % 3 ? "#2c3440" : "#3a4655";           // окна чуть разные
        g.fillRect(14 + x * 62, 34 + y * 112, 40, 78);
      }
      const t = new THREE.CanvasTexture(c);
      t.colorSpace = THREE.SRGBColorSpace;
      t.wrapS = t.wrapT = THREE.RepeatWrapping;
      this.facadeTexture = t;
    }
    const length = 900, height = 42; // длинные: тест на лося уезжает по улице на 100+ м
    const tex = this.facadeTexture.clone();
    tex.needsUpdate = true;
    tex.repeat.set(length / 7, height / 7); // окно ~1.6 м, этаж ~3.5 м
    const wall = new THREE.Mesh(new THREE.PlaneGeometry(length, height),
      new THREE.MeshStandardMaterial({ map: tex, roughness: 0.95 }));
    wall.position.set(150, height / 2 - 0.05, z);
    wall.rotation.y = facing > 0 ? 0 : Math.PI; // лицом к улице
    return wall;
  }

  contactShadow() {
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const g = c.getContext("2d");
    const grad = g.createRadialGradient(64, 64, 10, 64, 64, 64);
    grad.addColorStop(0, "rgba(0,0,0,0.65)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 128);
    const m = new THREE.Mesh(new THREE.PlaneGeometry(2.6, 5.2),
      new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }));
    m.rotation.x = -Math.PI / 2;
    m.position.y = 0.01;
    return m;
  }

  /** Загрузить машину (на экране загрузки). Возвращается, когда всё готово к показу. */
  async prepare(car, colorName) {
    await this.envReady;
    this.setHover(null);
    if (this.car) this.carRoot.remove(this.car);
    this.hotspots = car.hotspots || {};
    const color = COLORS[colorName] ?? COLORS[(car.colors || [])[0]] ?? COLORS.gray;
    let model = null;
    try {
      if (car.model_url) model = await this.loadModel(car.model_url);
      this.parts = car.parts_url ? await this.loadParts(car.parts_url) : new Map();
    } catch (e) {
      console.warn("3D load failed", e);
    }
    this.car = model || this.buildPlaceholder();
    this.isModel = !!model;
    if (!model) this.parts = new Map(PLACEHOLDER_PARTS.map((p) => [p.id, p]));
    this.modelRoot = this.car.children[0] || this.car;
    this.indexParts();
    this.paint(color);
    this.carRoot.add(this.car);
    this.measure();
    // В салон можно зайти всегда: «Место водителя» есть у любой модели (камера по рулю/креслу или по пропорциям машины).
    // Шаг «Управление» — только если нашлись рычаг, ручник, консоль или педали.
    const hasCabin = (s) => s.parts.some((id) => this.parts.get(id)?.group === "interior");
    TOUR.splice(0, TOUR.length, ...ALL_TOUR.filter((s) => !s.interior || s.id === "cabin" || hasCabin(s)));
    this.poses = TOUR.map((s) => ({ ...s.pose(this), hfov: s.hfov, interior: !!s.interior }));
  }

  /** Показать: экскурсия с начала + анимация появления. */
  start() {
    this.p = this.targetP = 0;
    this.yaw = this.pitch = 0;
    this.occluded.clear();
    this.appear();
  }

  async loadModel(url) {
    if (!this.cache.has(url)) {
      const isFbx = url.toLowerCase().endsWith(".fbx");
      const obj = isFbx ? await new FBXLoader().loadAsync(url) : (await gltfLoader.loadAsync(url)).scene;
      // нормализуем: длина ~4.2 м, стоит на земле по центру
      let box = new THREE.Box3().setFromObject(obj);
      let size = box.getSize(new THREE.Vector3());
      if (url.includes("/models/sf/") && size.x > size.z) {  // модель с Sketchfab: длиной вдоль оси Z, как наши
        obj.rotateY(Math.PI / 2);
        obj.updateMatrixWorld(true);
        box = new THREE.Box3().setFromObject(obj);
        size = box.getSize(new THREE.Vector3());
      }
      obj.scale.multiplyScalar(4.2 / Math.max(size.x, size.z));
      const b2 = new THREE.Box3().setFromObject(obj);
      const c = b2.getCenter(new THREE.Vector3());
      obj.position.sub(new THREE.Vector3(c.x, b2.min.y, c.z));
      this.cache.set(url, obj);
    }
    const g = new THREE.Group();
    g.add(this.cache.get(url).clone(true));
    return g;
  }

  async loadParts(url) {
    if (!this.cache.has(url)) this.cache.set(url, await fetchJson(url));
    return new Map(this.cache.get(url).parts.map((p) => [p.id, p]));
  }

  indexParts() {
    const nodeToPart = new Map();
    for (const p of this.parts.values()) for (const n of p.nodes) nodeToPart.set(n, p.id);
    this.partMeshes = new Map();
    this.car.traverse((o) => {
      if (!o.isMesh) return;
      const ids = [];                       // ближайшая деталь (шина) и её группы (колесо целиком)
      for (let a = o; a; a = a.parent) { const id = nodeToPart.get(a.name); if (id && !ids.includes(id)) ids.push(id); }
      const near = ids[0] ?? null;
      const parent = near && this.parts.get(near)?.parent;
      if (parent && !ids.includes(parent)) ids.push(parent);
      o.userData.partId = near;
      for (const id of ids) (this.partMeshes.get(id) ?? this.partMeshes.set(id, []).get(id)).push(o);
    });
  }

  /** Модель без текстур (белая «болванка»)? Тогда её красим сами по деталям, которые нашёл разбивщик. */
  isPlain() {
    let maps = 0, colored = 0, total = 0;
    this.car.traverse((o) => {
      if (!o.isMesh) return;
      o.userData.origMat ??= o.material;
      for (const m of (Array.isArray(o.userData.origMat) ? o.userData.origMat : [o.userData.origMat])) {
        total++;
        if (m.map) maps++;
        const c = m.color;
        if (c && Math.max(c.r, c.g, c.b) - Math.min(c.r, c.g, c.b) > 0.12) colored++;
      }
    });
    return total > 0 && maps === 0 && colored / total < 0.15;
  }

  paintByParts(hex) {
    const kind = (id) => !id ? "keep"
      : /^(body_paint|door_|fender_|hood|roof|trunk_lid|front_bumper|rear_bumper|mirror_|tailgate|quarter)/.test(id) ? "paint"
      : /(windshield|window|glass)/.test(id) ? "glass"
      : /^(wheel_|tire_)/.test(id) ? "wheel"
      : /^(rim_|handle_|badge_|exhaust)/.test(id) ? "chrome"
      : /^brake_/.test(id) ? "brake"
      : /^(sill_|pillar_)/.test(id) ? "paint"
      : /^fog_light/.test(id) ? "head"
      : /^plate_/.test(id) ? "plate"
      : /^headlight/.test(id) ? "head"
      : /^taillight/.test(id) ? "tail"
      : this.parts.get(id)?.group === "interior" ? "cabin" : "keep";     // нераспознанное и непонятное — как в исходной модели
    const mats = {
      paint: new THREE.MeshPhysicalMaterial({ color: hex, metalness: 0.55, roughness: 0.32, clearcoat: 1, clearcoatRoughness: 0.08 }),
      glass: new THREE.MeshPhysicalMaterial({ color: 0x0d1217, metalness: 0, roughness: 0.05, transparent: true, opacity: 0.55, depthWrite: false }),
      wheel: new THREE.MeshStandardMaterial({ color: 0x24262a, metalness: 0.7, roughness: 0.45 }),
      head: new THREE.MeshStandardMaterial({ color: 0xe8eef5, metalness: 0.2, roughness: 0.1, emissive: 0x4a4f55 }),
      tail: new THREE.MeshStandardMaterial({ color: 0x8e0f12, roughness: 0.25, emissive: 0x3a0405 }),
      cabin: new THREE.MeshStandardMaterial({ color: 0x2b2724, roughness: 0.85 }),
      chrome: new THREE.MeshStandardMaterial({ color: 0xc9ccd1, metalness: 1, roughness: 0.18 }),
      brake: new THREE.MeshStandardMaterial({ color: 0x8a1a1a, metalness: 0.5, roughness: 0.5 }),
      plate: new THREE.MeshStandardMaterial({ color: 0xf1f1ee, roughness: 0.6 }),
      trim: new THREE.MeshStandardMaterial({ color: 0x1d1f23, metalness: 0.3, roughness: 0.6 }),
    };
    const orig = (o) => (Array.isArray(o.userData.origMat) ? o.userData.origMat : [o.userData.origMat]);
    // материалы, которыми покрыты уверенно найденные панели, — это и есть «краска» этой модели
    const bodyMats = new Set();
    const bodyCols = [];
    this.car.traverse((o) => {
      if (o.isMesh && kind(o.userData.partId) === "paint") for (const m of orig(o)) { bodyMats.add(m.uuid); if (m.color) bodyCols.push(m.color); }
    });
    const isBody = (o) => orig(o).some((m) => bodyMats.has(m.uuid) || (m.color && !m.map && bodyCols.some((c) => Math.abs(c.r - m.color.r) + Math.abs(c.g - m.color.g) + Math.abs(c.b - m.color.b) < 0.09)));
    this.car.traverse((o) => {
      if (!o.isMesh) return;
      let k = kind(o.userData.partId);
      if (k === "keep" && isBody(o)) k = "paint";          // неразмеченные куски того же материала красим так же, чтобы не было пятен
      if (k === "keep") { o.material = o.userData.origMat; return; }
      const n = orig(o).length;
      const m = mats[k];
      o.material = n > 1 ? Array(n).fill(m) : m;
    });
  }

  paint(hex) {
    if (this.parts?.size && this.isModel && this.isPlain() && [...this.parts.keys()].some((k) => k.startsWith("door_") || k.startsWith("wheel_"))) {
      this.paintByParts(hex);
      return;
    }
    this.car.traverse((o) => {
      if (!o.isMesh) return;
      const mats = materialsOf(o).map((m) => {
        const c = m.clone();
        if (PAINT_MATERIALS.includes(matName(m))) c.color?.setHex(hex);
        if (GLASS_MATERIALS.includes(matName(m))) Object.assign(c, { transparent: true, opacity: 0.3, depthWrite: false });
        return c;
      });
      o.material = mats.length === 1 ? mats[0] : mats;
    });
  }

  // ---------- геометрия машины для камеры ----------
  boxOfPart(id) {
    // Габариты берём из разметки (parts.json): у разбитой модели все детали делят один буфер вершин, и по самим мешам
    // рамка получается размером со всю исходную геометрию, а не с деталь.
    const bb = this.parts.get(id)?.bbox;
    if (bb && this.modelRoot) {
      this.modelRoot.updateWorldMatrix(true, false);
      const box = new THREE.Box3();
      for (const x of [bb.min[0], bb.max[0]]) for (const y of [bb.min[1], bb.max[1]]) for (const z of [bb.min[2], bb.max[2]]) {
        box.expandByPoint(this.modelRoot.localToWorld(new THREE.Vector3(x, y, z)));
      }
      return box;
    }
    const meshes = this.partMeshes.get(id);
    if (!meshes?.length) return null;
    const b = new THREE.Box3();
    meshes.forEach((m) => b.expandByObject(m));
    return b;
  }

  partCenter(id) {
    return this.boxOfPart(id)?.getCenter(new THREE.Vector3()) ?? this.center.clone();
  }

  /** Точка для сноски: anchor из parts.json (в координатах модели) или центр детали */
  anchorOf(id) {
    const a = this.parts.get(id)?.anchor;
    if (a && this.modelRoot) return this.modelRoot.localToWorld(new THREE.Vector3(...a));
    return this.boxOfPart(id)?.getCenter(new THREE.Vector3()) ?? null;
  }

  measure() {
    this.carRoot.updateMatrixWorld(true);
    this.box = new THREE.Box3().setFromObject(this.car);
    this.center = this.box.getCenter(new THREE.Vector3());
    const l = this.boxOfPart("headlight_left"), r = this.boxOfPart("headlight_right");
    const ref = (l && r ? l.union(r) : null) || this.boxOfPart("hood");
    const front = ref ? ref.getCenter(new THREE.Vector3()).sub(this.center)
      : new THREE.Vector3(0, 0, -1).applyQuaternion(this.carRoot.quaternion);
    front.y = 0;
    this.front = front.normalize();
    this.left = UP.clone().cross(this.front).normalize();
  }

  ground(h = 0) { return new THREE.Vector3(this.center.x, this.box.min.y + h, this.center.z); }

  /** Камера снаружи: f — вперёд, l — влево, u — вверх (метры от центра машины на земле) */
  ext(f, l, u, target) {
    const pos = this.ground(u).addScaledVector(this.front, f).addScaledVector(this.left, l);
    return { pos, target };
  }

  /** Глаза водителя: с переднего сиденья, как сидит человек. Опора — руль, затем кресло; любое найденное положение проверяется
   *  по пропорциям машины (голова водителя — примерно 36–60% длины от носа), иначе берётся типичное место водителя. */
  driverEye() {
    const size = this.box.getSize(new THREE.Vector3());
    const L = Math.abs(size.x * this.front.x) + Math.abs(size.z * this.front.z);
    const W = Math.abs(size.x * this.left.x) + Math.abs(size.z * this.left.z);
    const H = size.y;
    const fromNose = (p) => (L / 2 - p.clone().sub(this.center).dot(this.front)) / L;
    const sane = (p) => p && fromNose(p) > 0.36 && fromNose(p) < 0.6;
    const small = (b) => b && ["x", "y", "z"].every((k) => b.max[k] - b.min[k] < 1.2);   // не больше метра по каждой оси
    let eye = null;
    const wheel = this.boxOfPart("steering_wheel");
    if (small(wheel)) {                              // голова ≈ 0,55 м позади центра руля и на 0,28 м выше
      const c = wheel.getCenter(new THREE.Vector3());
      const p = c.clone().addScaledVector(this.front, -0.55);
      p.y = c.y + 0.28;
      if (sane(p)) eye = p;
    }
    const seat = this.boxOfPart("seat_driver");
    if (!eye && seat && small(seat)) {               // над подушкой, у спинки — там, где голова, а не в подголовнике
      const c = seat.getCenter(new THREE.Vector3());
      const sz = seat.getSize(new THREE.Vector3());
      const depth = Math.abs(sz.x * this.front.x) + Math.abs(sz.z * this.front.z);
      const p = c.clone().addScaledVector(this.front, depth * 0.05);
      p.y = seat.min.y + Math.min(sz.y * 0.85, 0.72);
      if (sane(p)) eye = p;
    }
    if (!eye) {                                      // ничего надёжного не нашлось: типичное место водителя
      eye = this.center.clone().addScaledVector(this.front, L / 2 - 0.47 * L);
      eye.y = this.box.min.y + H * 0.72;
    }
    const lateral = eye.clone().sub(this.center).dot(this.left);
    if (lateral < 0.1 * W || lateral > 0.32 * W) eye.addScaledVector(this.left, 0.2 * W - lateral);   // водитель слева, а не по центру или у двери
    eye.y = Math.min(Math.max(eye.y, this.box.min.y + 0.55 * H), this.box.min.y + 0.82 * H);
    return eye;
  }

  /** точка взгляда из салона: вперёд, чуть вправо (к центру) и вниз */
  lookAhead(right, down) {
    return this.driverEye().addScaledVector(this.front, 1).addScaledVector(this.left, -right).add(new THREE.Vector3(0, down, 0));
  }

  cabin(target) { return { pos: this.driverEye(), target }; }

  // ---------- экскурсия ----------
  setProgress(p) { this.targetP = clamp(p, 0, TOUR.length - 1); }
  stopIndex() { return Math.round(this.p); }

  poseAt(p) {
    const i = Math.min(Math.floor(p), this.poses.length - 1);
    const j = Math.min(i + 1, this.poses.length - 1);
    const t = smooth(p - i);
    const A = this.poses[i], B = this.poses[j];
    let pos;
    if (A.interior !== B.interior) {
      // вход/выход из салона — по дуге через водительскую дверь, а не сквозь крышу
      const door = this.ground(1.25).addScaledVector(this.left, 2.0);
      const u = 1 - t;
      pos = A.pos.clone().multiplyScalar(u * u).addScaledVector(door, 2 * u * t).addScaledVector(B.pos, t * t);
    } else {
      pos = A.pos.clone().lerp(B.pos, t);
    }
    const target = A.target.clone().lerp(B.target, t);
    const hfov = A.hfov + (B.hfov - A.hfov) * t;
    return { pos, target, hfov, interior: (t < 0.5 ? A : B).interior };
  }

  applyCamera() {
    if (!this.poses) return;
    if (Math.abs(this.targetP - this.p) > 0.002) {
      this.p += (this.targetP - this.p) * 0.12;
      this.yaw *= 0.9; this.pitch *= 0.9; // листаем — возвращаемся к заданному ракурсу
    } else this.p = this.targetP;
    const { pos, target, hfov, interior } = this.poseAt(this.p);
    if (interior !== this.interior) this.setHover(null);
    this.interior = interior;

    // перетаскивание: снаружи — облёт вокруг точки, в салоне — поворот головы
    if (interior) {
      const dir = target.clone().sub(pos);
      const right = dir.clone().cross(UP).normalize();
      dir.applyAxisAngle(UP, this.yaw).applyAxisAngle(right.applyAxisAngle(UP, this.yaw), this.pitch);
      target.copy(pos).add(dir);
    } else {
      pos.sub(target).applyAxisAngle(UP, this.yaw).add(target);
    }

    // горизонтальный угол обзора постоянный: на узком экране телефона машина не обрезается
    const aspect = this.camera.aspect;
    const maxV = interior ? 95 : 70;
    let vfov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(hfov) / 2) / aspect));
    if (vfov > maxV) {
      if (!interior) { // не влезает по ширине — отходим камерой назад
        const hEff = 2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(maxV) / 2) * aspect);
        const k = Math.tan(THREE.MathUtils.degToRad(hfov) / 2) / Math.tan(hEff / 2);
        pos.sub(target).multiplyScalar(k).add(target);
        pos.y = Math.max(pos.y, this.box.min.y + 0.3);
      }
      vfov = maxV;
    }
    const cor = ENVIRONMENT.corridor; // снаружи камера остаётся над улицей, а не внутри домов
    if (!interior && cor) pos.z = clamp(pos.z, cor.zMin, cor.zMax);
    if (Math.abs(this.camera.fov - vfov) > 0.01) { this.camera.fov = vfov; this.camera.updateProjectionMatrix(); }
    this.camera.position.copy(pos);
    this.camera.lookAt(target);
  }

  appear() {
    const t0 = performance.now();
    this.anim = (now) => {
      const t = Math.min(1, (now - t0) / 1100);
      this.car.scale.setScalar(0.2 + 0.8 * ease(t));
      this.car.rotation.y = (1 - ease(t)) * Math.PI * 1.2;
      return t < 1;
    };
  }

  // ---------- ввод ----------
  bindPointer() {
    const el = this.renderer.domElement;
    let drag = null;
    el.addEventListener("pointerdown", (e) => {
      drag = { x: e.clientX, y: e.clientY, yaw: this.yaw, pitch: this.pitch, moved: false };
      this.onHover(e); // тап на телефоне = показать сноску
    });
    el.addEventListener("pointermove", (e) => {
      if (drag && (e.buttons || e.pointerType === "touch")) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (this.test) {
          if (dx) this.test.orbitBy(-dx * 0.006);
          drag.x = e.clientX;
          return;
        }
        if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
        this.yaw = drag.yaw - dx * 0.006;
        if (e.pointerType === "mouse") this.pitch = clamp(drag.pitch - dy * 0.004, -0.7, 0.7);
        if (!this.interior) this.pitch = 0;
      } else this.onHover(e);
    });
    const end = () => { drag = null; };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end); // браузер забрал жест под прокрутку
    el.addEventListener("pointerleave", () => { end(); this.setHover(null); });
  }

  onHover(e) {
    if (!this.car || this.test) return;
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hit = this.raycaster.intersectObject(this.car, true)
      .find((h) => h.object.userData.partId && !this.skipPick(h.object));
    const id = hit?.object.userData.partId;
    this.setHover(id && this.visibleFromHere(id) ? id : null);
  }

  // снаружи — только внешние детали, из салона — только салон
  visibleFromHere(id) {
    const group = this.parts.get(id)?.group;
    return !group || group === (this.interior ? "interior" : "exterior");
  }

  /** Цветной слой поверх детали: key — независимые слои (наведение, повреждения в тесте на лося) */
  highlight(id, on, material = HIGHLIGHT_MATERIAL, key = "overlay") {
    for (const mesh of this.partMeshes.get(id) || []) {
      if (on && !mesh.userData[key]) {
        const overlay = new THREE.Mesh(mesh.geometry, material);
        overlay.raycast = () => {}; // слой не должен перехватывать наведение
        overlay.renderOrder = 10;
        mesh.add(overlay);
        mesh.userData[key] = overlay;
      } else if (!on && mesh.userData[key]) {
        mesh.remove(mesh.userData[key]);
        mesh.userData[key] = null;
      }
    }
  }

  /** Стекло — не деталь: сквозь него наводятся на то, что за ним (салон, сиденья). */
  skipPick(o) {
    return isGlass(o) || this.parts.get(o.userData.partId)?.pick === false;
  }

  setHover(id) {
    if (this.hoveredPart === id) return;
    if (this.hoveredPart) this.highlight(this.hoveredPart, false);
    this.hoveredPart = id || null;
    if (id) this.highlight(id, true);
  }

  // ---------- сноски ----------
  activeCallouts() {
    const out = new Map(); // id -> прозрачность
    TOUR.forEach((s, i) => {
      const a = 1 - Math.abs(this.p - i) / 0.4;
      if (a > 0) s.parts.forEach((id) => out.set(id, Math.max(out.get(id) || 0, a)));
    });
    if (this.hoveredPart) out.set(this.hoveredPart, 1);
    return out;
  }

  isOccluded(id, point) {
    if (this.frame % 10 !== 0 && this.occluded.has(id)) return this.occluded.get(id);
    const dir = point.clone().sub(this.camera.position);
    const dist = dir.length();
    this.raycaster.set(this.camera.position, dir.normalize());
    this.raycaster.far = dist;
    const hit = this.raycaster.intersectObject(this.car, true)
      .find((h) => !this.skipPick(h.object) && h.distance < dist - 0.06 && h.object.userData.partId !== id
        && this.parts.get(h.object.userData.partId)?.parent !== id);
    this.raycaster.far = Infinity;
    this.occluded.set(id, !!hit);
    return !!hit;
  }

  updateCallouts() {
    const W = this.container.clientWidth, H = this.container.clientHeight;
    const items = [];
    for (const [id, alpha] of this.activeCallouts()) {
      const part = this.parts.get(id);
      const p3 = part && this.anchorOf(id);
      if (!p3 || !this.visibleFromHere(id)) continue;
      const pr = p3.clone().project(this.camera);
      if (pr.z > 1 || Math.abs(pr.x) > 0.98 || Math.abs(pr.y) > 0.95) continue;
      if (id !== this.hoveredPart && this.isOccluded(id, p3)) continue;
      items.push({ id, part, alpha, x: (pr.x + 1) / 2 * W, y: (1 - pr.y) / 2 * H });
    }

    // переиспользуем DOM-элементы сносок
    const existing = new Map([...this.callouts.children].map((el) => [el.dataset.id, el]));
    for (const [id, el] of existing) if (!items.some((it) => it.id === id)) el.remove();
    for (const it of items) {
      let el = existing.get(it.id);
      if (!el) {
        el = document.createElement("div");
        el.className = "callout";
        el.dataset.id = it.id;
        const b = document.createElement("b");
        b.textContent = it.part.label_ru;
        el.append(b);
        const fact = this.hotspots[it.id] ?? this.hotspots[it.part.parent];
        const text = fact || aboutPart(it.id);           // реальные данные этой машины, иначе — зачем деталь
        if (text) { const s = document.createElement("span"); s.textContent = text; if (!fact) s.className = "about"; el.append(s); }
        this.callouts.append(el);
      }
      it.el = el;
      it.w = el.offsetWidth; it.h = el.offsetHeight;
    }

    // раскладка: подпись рядом с деталью — короткая выноска наружу от центра машины
    const margin = 12, top = 136, bottom = H - 88, reach = 46; // сверху — заголовок и кнопка теста на лося
    const c = this.center.clone().project(this.camera);
    const inView = !this.interior && c.z < 1; // в салоне центр машины за камерой — берём центр экрана
    const cx = inView ? (c.x + 1) / 2 * W : W / 2, cy = inView ? (1 - c.y) / 2 * H : H / 2;
    for (const it of items) {
      let dx = it.x - cx, dy = it.y - cy;
      const len = Math.hypot(dx, dy);
      if (len < 1) { dx = 1; dy = -1; } // деталь в центре кадра — выноска вправо-вверх
      const k = reach / (Math.hypot(dx, dy) || 1);
      const ex = it.x + dx * k, ey = it.y + dy * k - 10; // конец выноски, чуть выше точки
      it.right = dx >= 0; // подпись справа от точки (линия входит в её левый край)
      it.lx = clamp(it.right ? ex : ex - it.w, margin, W - margin - it.w);
      it.ly = clamp(ey - it.h / 2, top, bottom - it.h);
    }
    // подпись не должна закрывать свою же точку (у краёв экрана clamp мог её туда сдвинуть)
    const covers = (it, pad = 12) => it.x > it.lx - pad && it.x < it.lx + it.w + pad
      && it.y > it.ly - pad && it.y < it.ly + it.h + pad;
    for (const it of items) {
      if (!covers(it)) continue;
      const above = it.y - 16 - it.h, below = it.y + 16;
      it.ly = above >= top ? above : clamp(below, top, bottom - it.h);
    }
    // раздвигаем наложившиеся подписи по вертикали
    const placed = [];
    for (const it of [...items].sort((a, b) => a.ly - b.ly)) {
      for (let guard = 0; guard < 8; guard++) {
        const hit = placed.find((p) => it.lx < p.lx + p.w + 6 && p.lx < it.lx + it.w + 6
          && it.ly < p.ly + p.h + 6 && p.ly < it.ly + it.h + 6);
        if (!hit) break;
        it.ly = hit.ly + hit.h + 6;
        if (it.ly + it.h > bottom) it.ly = Math.max(top, hit.ly - it.h - 6);
      }
      placed.push(it);
    }
    let svg = "";
    for (const it of items) {
      it.el.style.transform = `translate(${it.lx}px, ${it.ly}px)`;
      it.el.style.opacity = it.alpha.toFixed(2);
      it.el.classList.toggle("active", it.id === this.hoveredPart);
      // выноска с изломом: от точки — к середине бокового края подписи, последний отрезок горизонтальный
      const leftEdge = it.x <= it.lx + it.w / 2;           // подпись правее точки → входим в левый край
      const ex = leftEdge ? it.lx : it.lx + it.w, ey = it.ly + it.h / 2;
      const kx = ex + (leftEdge ? -1 : 1) * Math.min(14, Math.abs(ex - it.x) / 2);
      const f = (n) => n.toFixed(1);
      svg += `<g opacity="${it.alpha.toFixed(2)}"><polyline points="${f(it.x)},${f(it.y)} ${f(kx)},${f(ey)} ${f(ex)},${f(ey)}"/>`
        + `<circle cx="${f(it.x)}" cy="${f(it.y)}" r="4"/></g>`;
    }
    this.lines.innerHTML = svg;
  }

  // ---------- цикл ----------
  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.lines.setAttribute("viewBox", `0 0 ${w} ${h}`);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  tick() {
    if (!this.container.clientWidth) return; // экран с 3D скрыт — не тратим GPU
    this.frame++;
    const now = performance.now();
    // настоящее время кадра; потолок 0,25 с — только чтобы вкладка, вернувшаяся из фона, не «прыгала»
    const dt = Math.min(0.25, (now - (this.lastT ?? now)) / 1000);
    this.lastT = now;
    if (this.anim && !this.anim(now)) this.anim = null;
    if (this.test) { // тест на лося сам двигает машину и камеру в этой же сцене (фиксированный шаг по dt)
      this.test.update(dt);
      this.renderer.render(this.scene, this.camera);
      return;
    }
    this.applyCamera();
    this.updateTreeFade();
    this.renderer.render(this.scene, this.camera);
    if (this.car) this.updateCallouts();
  }

  buildPlaceholder() {
    const g = new THREE.Group();
    const paint = new THREE.MeshStandardMaterial({ name: "paint", metalness: 0.5, roughness: 0.35 });
    const dark = new THREE.MeshStandardMaterial({ color: 0x15171c, roughness: 0.8 });
    const add = (name, geo, mat, x, y, z) => {
      const m = new THREE.Mesh(geo, mat); m.name = name; m.position.set(x, y, z); g.add(m); return m;
    };
    add("body_paint", new THREE.BoxGeometry(1.8, 0.6, 4.2), paint, 0, 0.6, 0);
    add("hood", new THREE.BoxGeometry(1.7, 0.04, 1.1), paint, 0, 0.92, -1.45);
    for (const [x, z, n] of [[-0.85, -1.3, "wheel_front_left"], [0.85, -1.3, "wheel_front_right"],
      [-0.85, 1.3, "wheel_rear_left"], [0.85, 1.3, "wheel_rear_right"]]) {
      add(n, new THREE.CylinderGeometry(0.33, 0.33, 0.24, 28), dark, x, 0.33, z).rotation.z = Math.PI / 2;
    }
    add("headlight_left", new THREE.BoxGeometry(0.42, 0.12, 0.04),
      new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0xfff3c4 }), -0.6, 0.75, -2.11);
    return g;
  }
}

const PLACEHOLDER_PARTS = [
  { id: "body_paint", label_ru: "Кузов", nodes: ["body_paint"] },
  { id: "hood", label_ru: "Капот", nodes: ["hood"] },
  { id: "headlight_left", label_ru: "Фара", nodes: ["headlight_left"] },
  ...["wheel_front_left", "wheel_front_right", "wheel_rear_left", "wheel_rear_right"]
    .map((id) => ({ id, label_ru: "Колесо", nodes: [id] })),
];
