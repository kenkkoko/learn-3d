// 3D 知識星雲：起點在中心、越外層越進階、同一分支同一方向（SPEC P0-4）
// 場景寫法沿用 creativity-3d：Three.js 0.170 + OrbitControls + CSS2DRenderer 標籤
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

export const STATUS_COLOR = {
  unseen: "#7a7a85",
  weak: "#f87171",
  shaky: "#facc15",
  mastered: "#4ade80",
};
const ACCENT = new THREE.Color("#f0a46a");
const LOCKED_COLOR = new THREE.Color("#3a3a42");
const STEP = 2.6; // 每一層的半徑間距（場景單位）

const easeOutExpo = (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));

function hash01(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) h = Math.imul(h ^ str.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10000) / 10000;
}

function glowTexture() {
  const size = 128;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  grd.addColorStop(0, "rgba(255,255,255,1)");
  grd.addColorStop(0.15, "rgba(255,255,255,.85)");
  grd.addColorStop(0.4, "rgba(255,255,255,.22)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, size, size);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// CSS2DRenderer 會覆寫外層元素的 transform，所以樣式放在內層
function makeLabel(cls, text) {
  const wrap = document.createElement("div");
  const inner = document.createElement("div");
  inner.className = cls;
  inner.textContent = text;
  wrap.appendChild(inner);
  return { obj: new CSS2DObject(wrap), el: inner };
}

export function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl")));
  } catch {
    return false;
  }
}

/**
 * 放射狀樹狀配置：主要先修（層數最高的那個）當父節點，
 * 每棵子樹依葉子數分到一段連續角度 → 同一分支朝同一方向。
 */
export function layoutNodes(nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const minLayer = Math.min(...nodes.map((n) => n.layer));
  const parent = new Map();
  const children = new Map(nodes.map((n) => [n.id, []]));
  const roots = [];
  for (const n of nodes) {
    const ps = (n.prereqs || []).map((id) => byId.get(id)).filter((p) => p && p.layer < n.layer);
    if (!ps.length) { roots.push(n); continue; }
    const p = ps.reduce((a, b) => (b.layer > a.layer ? b : a));
    parent.set(n.id, p.id);
    children.get(p.id).push(n);
  }
  const leaves = new Map();
  const countLeaves = (n) => {
    const ch = children.get(n.id);
    const v = ch.length ? ch.reduce((s, c) => s + countLeaves(c), 0) : 1;
    leaves.set(n.id, v);
    return v;
  };
  roots.forEach(countLeaves);
  const pos = new Map();
  const place = (n, a0, a1) => {
    const mid = (a0 + a1) / 2;
    const depth = n.layer - minLayer;
    let r = depth * STEP;
    if (depth === 0 && roots.length > 1) r = STEP * 0.45;
    const y = depth === 0 ? 0 : (hash01(n.id) - 0.5) * Math.min(3.2, 0.7 * depth + 0.4);
    pos.set(n.id, new THREE.Vector3(Math.cos(mid) * r, y, Math.sin(mid) * r));
    const ch = children.get(n.id);
    const total = ch.reduce((s, c) => s + leaves.get(c.id), 0);
    let a = a0;
    for (const c of ch) {
      const span = ((a1 - a0) * leaves.get(c.id)) / total;
      place(c, a, a + span);
      a += span;
    }
  };
  const totalRoots = roots.reduce((s, r) => s + leaves.get(r.id), 0);
  let a = -Math.PI / 2;
  for (const r of roots) {
    const span = (Math.PI * 2 * leaves.get(r.id)) / totalRoots;
    place(r, a, a + span);
    a += span;
  }
  return { pos, minLayer, maxLayer: Math.max(...nodes.map((n) => n.layer)) };
}

/**
 * @param {HTMLElement} container
 * @param {{nodes: Array<{id,name,layer,prereqs,status,locked}>, current: string|null}} data
 * @param {{onSelect?: (id: string|null) => void}} opts
 */
export function createNebula(container, data, opts = {}) {
  const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  let reduceMotion = !!mq?.matches;
  let width = container.clientWidth || 800;
  let height = container.clientHeight || 600;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, height);
  renderer.setClearColor(0x000000, 0);
  renderer.domElement.setAttribute("aria-hidden", "true");
  container.appendChild(renderer.domElement);

  const labelRenderer = new CSS2DRenderer();
  labelRenderer.setSize(width, height);
  Object.assign(labelRenderer.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none", zIndex: "2" });
  labelRenderer.domElement.setAttribute("aria-hidden", "true");
  container.appendChild(labelRenderer.domElement);

  const { pos, minLayer, maxLayer } = layoutNodes(data.nodes);
  let maxR = 1;
  pos.forEach((v) => { maxR = Math.max(maxR, Math.hypot(v.x, v.z)); });

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x08080a, 0.9 / (maxR * 6));
  const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 500);
  // 依視窗比例算出能放下整片星雲的距離（俯角約 40°，看得出層與分支）
  const homePos = new THREE.Vector3();
  function fitCamera() {
    const vfov = THREE.MathUtils.degToRad(camera.fov);
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    const need = (maxR + 1.2) / Math.tan(Math.min(vfov, hfov) / 2);
    const dir = new THREE.Vector3(0, 0.66, 1).normalize();
    homePos.copy(dir.multiplyScalar(Math.max(6, need * (camera.aspect < 1 ? 0.9 : 0.72))));
  }
  fitCamera();
  camera.position.copy(homePos);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.minDistance = 3;
  controls.maxDistance = maxR * 5;
  controls.autoRotate = !reduceMotion;
  controls.autoRotateSpeed = 0.3;
  controls.enablePan = false;

  const tex = glowTexture();
  const disposables = [tex];
  const track = (o) => (disposables.push(o), o);

  // ---- 遠方背景星 ----
  {
    const n = 800;
    const arr = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const v = new THREE.Vector3().randomDirection().multiplyScalar(maxR * 5 + Math.random() * maxR * 6);
      arr.set([v.x, v.y, v.z], i * 3);
    }
    const g = track(new THREE.BufferGeometry());
    g.setAttribute("position", new THREE.BufferAttribute(arr, 3));
    const m = track(new THREE.PointsMaterial({ color: 0x9a9aa6, size: 0.35, sizeAttenuation: true, transparent: true, opacity: 0.45, map: tex, depthWrite: false, fog: false }));
    scene.add(new THREE.Points(g, m));
  }

  // ---- 層環：越外層越進階 ----
  for (let L = minLayer + 1; L <= maxLayer; L++) {
    const r = (L - minLayer) * STEP;
    const pts = [];
    for (let k = 0; k <= 128; k++) {
      const a = (k / 128) * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r));
    }
    const g = track(new THREE.BufferGeometry().setFromPoints(pts));
    const m = track(new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.045, depthWrite: false }));
    scene.add(new THREE.Line(g, m));
    const lbl = makeLabel("ring-label", `第 ${L} 層`).obj;
    lbl.position.set(r * Math.SQRT1_2, 0, r * Math.SQRT1_2);
    scene.add(lbl);
  }

  // ---- 先修連線（略彎的曲線） ----
  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  const edgeList = [];
  for (const n of data.nodes) {
    for (const p of n.prereqs || []) if (byId.has(p) && pos.has(p)) edgeList.push([p, n.id]);
  }
  function curvePoints(a, b) {
    const pa = pos.get(a), pb = pos.get(b);
    const mid = pa.clone().add(pb).multiplyScalar(0.5);
    const ctrl = mid.clone().multiplyScalar(1.06).add(new THREE.Vector3(0, 0.25, 0));
    return new THREE.QuadraticBezierCurve3(pa, ctrl, pb).getPoints(16);
  }
  const edgeCurves = edgeList.map(([a, b]) => curvePoints(a, b));
  let edgeLines;
  {
    const arr = [];
    edgeCurves.forEach((pts) => { for (let i = 0; i < pts.length - 1; i++) arr.push(pts[i], pts[i + 1]); });
    const g = track(new THREE.BufferGeometry().setFromPoints(arr));
    const m = track(new THREE.LineBasicMaterial({ color: 0xc3c3c8, transparent: true, opacity: 0.16, depthWrite: false }));
    edgeLines = new THREE.LineSegments(g, m);
    scene.add(edgeLines);
  }
  const highlight = new THREE.Group();
  scene.add(highlight);

  // ---- 節點 ----
  const stars = [];
  const starById = new Map();
  const hitTargets = [];
  data.nodes.forEach((n, i) => {
    const target = pos.get(n.id) || new THREE.Vector3();
    const group = new THREE.Group();
    scene.add(group);
    const isRoot = n.layer === minLayer;
    const glowMat = track(new THREE.SpriteMaterial({ map: tex, color: new THREE.Color(STATUS_COLOR.unseen), transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false }));
    const glow = new THREE.Sprite(glowMat);
    const baseScale = isRoot ? 1.6 : 1.05;
    glow.scale.setScalar(baseScale);
    group.add(glow);
    const coreMat = track(new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true }));
    const core = new THREE.Mesh(track(new THREE.SphereGeometry(isRoot ? 0.2 : 0.13, 20, 20)), coreMat);
    group.add(core);
    const haloMat = track(new THREE.SpriteMaterial({ map: tex, color: ACCENT, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
    const halo = new THREE.Sprite(haloMat);
    halo.scale.setScalar(baseScale * 2.6);
    group.add(halo);
    const hit = new THREE.Mesh(track(new THREE.SphereGeometry(0.5, 8, 8)), track(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, colorWrite: false })));
    hit.userData.id = n.id;
    group.add(hit);
    hitTargets.push(hit);
    const { obj: label, el } = makeLabel("node-label" + (isRoot ? " root" : ""), n.name);
    group.add(label);
    const s = { id: n.id, i, group, glow, core, halo, label, el, target, baseScale, status: n.status, locked: !!n.locked, delay: 0.15 + (n.layer - minLayer) * 0.18 + hash01(n.id) * 0.1 };
    stars.push(s);
    starById.set(n.id, s);
  });

  let currentId = data.current;
  let hovered = null;
  let selected = null;

  function applyState(s) {
    const c = new THREE.Color(STATUS_COLOR[s.status] || STATUS_COLOR.unseen);
    if (s.locked) {
      s.glow.material.color.copy(c).lerp(LOCKED_COLOR, 0.7);
      s.glow.material.opacity = 0.35;
      s.core.material.color.copy(c).lerp(LOCKED_COLOR, 0.75);
      s.core.material.opacity = 0.6;
    } else {
      s.glow.material.color.copy(c);
      s.glow.material.opacity = s.status === "unseen" ? 0.55 : 0.95;
      s.core.material.color.copy(c).lerp(new THREE.Color(0xffffff), 0.45);
      s.core.material.opacity = 1;
    }
    s.el.classList.toggle("locked", s.locked);
    s.el.dataset.status = s.status;
  }
  stars.forEach(applyState);

  function refreshLabels() {
    for (const s of stars) {
      // 每個節點一律顯示名稱；hover / 選取 / 目前節點只加強亮度
      s.el.classList.add("show");
      s.el.classList.toggle("hot", s === hovered);
      s.el.classList.toggle("on", s === selected);
      s.el.classList.toggle("current", s.id === currentId);
    }
  }

  function drawHighlight(s) {
    highlight.children.slice().forEach((o) => { highlight.remove(o); o.geometry?.dispose(); o.material?.dispose(); });
    if (!s) return;
    const arr = [];
    edgeList.forEach(([a, b], k) => {
      if (a !== s.id && b !== s.id) return;
      const pts = edgeCurves[k];
      for (let i = 0; i < pts.length - 1; i++) arr.push(pts[i], pts[i + 1]);
    });
    if (!arr.length) return;
    const g = new THREE.BufferGeometry().setFromPoints(arr);
    const m = new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.85, depthWrite: false });
    highlight.add(new THREE.LineSegments(g, m));
  }

  function setSelected(s, notify = true) {
    selected = s;
    refreshLabels();
    drawHighlight(s);
    edgeLines.material.opacity = s ? 0.08 : 0.16;
    if (notify) opts.onSelect?.(s ? s.id : null);
  }
  refreshLabels();

  // ---- 互動 ----
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  function pick(ev) {
    const r = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - r.left) / r.width) * 2 - 1;
    pointer.y = -((ev.clientY - r.top) / r.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(hitTargets, false)[0];
    return hit ? starById.get(hit.object.userData.id) : null;
  }
  function onMove(ev) {
    if (ev.pointerType === "touch") return;
    const s = pick(ev);
    renderer.domElement.style.cursor = s ? "pointer" : "grab";
    if (s !== hovered) { hovered = s; refreshLabels(); }
  }
  let downAt = null;
  function onDown(ev) { downAt = { x: ev.clientX, y: ev.clientY }; pauseAutoRotate(); }
  function onUp(ev) {
    if (!downAt) return;
    const moved = Math.hypot(ev.clientX - downAt.x, ev.clientY - downAt.y);
    downAt = null;
    if (moved > 6) return; // 拖曳不算點擊
    setSelected(pick(ev));
  }
  function onLeave() { if (hovered) { hovered = null; refreshLabels(); } }
  renderer.domElement.addEventListener("pointermove", onMove);
  renderer.domElement.addEventListener("pointerdown", onDown);
  renderer.domElement.addEventListener("pointerup", onUp);
  renderer.domElement.addEventListener("pointerleave", onLeave);
  renderer.domElement.style.cursor = "grab";
  renderer.domElement.style.touchAction = "none";

  let idleTimer = null;
  let userMoved = false;
  function pauseAutoRotate() {
    userMoved = true;
    controls.autoRotate = false;
    clearTimeout(idleTimer);
    if (!reduceMotion) idleTimer = setTimeout(() => { if (!selected) controls.autoRotate = true; }, 5000);
  }
  controls.addEventListener("start", pauseAutoRotate);
  renderer.domElement.addEventListener("wheel", pauseAutoRotate, { passive: true });

  const onMotionChange = () => {
    reduceMotion = !!mq?.matches;
    controls.autoRotate = !reduceMotion && !selected;
  };
  mq?.addEventListener?.("change", onMotionChange);

  // ---- 尺寸 ----
  function resize() {
    width = container.clientWidth || width;
    height = container.clientHeight || height;
    if (!width || !height) return;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    // 使用者還沒動過鏡頭時，跟著視窗重新對焦
    if (!userMoved) { fitCamera(); camera.position.copy(homePos); }
    renderer.setSize(width, height);
    labelRenderer.setSize(width, height);
  }
  const ro = new ResizeObserver(resize);
  ro.observe(container);

  // ---- 動畫迴圈 ----
  const t0 = performance.now();
  let raf = 0;
  let disposed = false;
  function frame(now) {
    if (disposed) return;
    raf = requestAnimationFrame(frame);
    const t = (now - t0) / 1000;
    for (const s of stars) {
      const k = reduceMotion ? 1 : easeOutExpo(Math.max(0, Math.min(1, (t - s.delay) / 1.4)));
      s.group.position.copy(s.target).multiplyScalar(k);
      const boost = s === selected ? 1.5 : s === hovered ? 1.25 : 1;
      const tw = reduceMotion ? 1 : 1 + Math.sin(t * 2.2 + s.i) * 0.04;
      s.glow.scale.setScalar(s.baseScale * boost * tw * Math.max(0.15, k));
      if (s.id === currentId) {
        const p = reduceMotion ? 0.5 : 0.5 + 0.5 * Math.sin(t * 2.4);
        s.halo.material.opacity = 0.25 + p * 0.45;
        s.halo.scale.setScalar(s.baseScale * (2.2 + p * 0.9));
      } else {
        s.halo.material.opacity = 0;
      }
    }
    edgeLines.visible = reduceMotion || t > 0.9;
    controls.update();
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  }
  raf = requestAnimationFrame(frame);

  return {
    /** 只更新狀態（顏色、鎖定、目前節點）；節點集合變了請重建 */
    update(next) {
      currentId = next.current;
      for (const n of next.nodes) {
        const s = starById.get(n.id);
        if (!s) continue;
        s.status = n.status;
        s.locked = !!n.locked;
        applyState(s);
      }
      refreshLabels();
    },
    select(id) {
      const s = id ? starById.get(id) || null : null;
      if (s) pauseAutoRotate();
      setSelected(s, false);
    },
    resetView() {
      camera.position.copy(homePos);
      controls.target.set(0, 0, 0);
    },
    get count() { return stars.length; },
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf);
      clearTimeout(idleTimer);
      ro.disconnect();
      mq?.removeEventListener?.("change", onMotionChange);
      drawHighlight(null);
      controls.dispose();
      renderer.domElement.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      renderer.domElement.removeEventListener("pointerleave", onLeave);
      disposables.forEach((d) => d.dispose?.());
      renderer.dispose();
      renderer.domElement.remove();
      labelRenderer.domElement.remove();
    },
  };
}
