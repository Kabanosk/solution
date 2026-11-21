// Finly title animation.
// Chubby toy dolphins float asleep on a soft pool. One by one they blink awake,
// colour fades in (each a different blue), then they swim around and leap out of the water.
// Restarts every time slide 1 becomes active.
import * as THREE from 'three';
import { RoomEnvironment } from './vendor/RoomEnvironment.js';

const canvas = document.getElementById('crabs');
const slide = canvas.closest('.slide');
const W = 1600, H = 900;
const STILL = !!document.documentElement.dataset.embed || matchMedia('(prefers-reduced-motion: reduce)').matches;

const notes = slide.querySelector('.notes');
if (notes) notes.textContent = "Hi, we're Finly. Let the pod wake up. Each dolphin is an AI agent for a part of your finance team: invoicing, payroll, procurement, expenses. Let us show you what they do.";

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(W, H, false);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.NeutralToneMapping;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.6;

const camera = new THREE.PerspectiveCamera(28, W / H, 0.1, 100);
camera.position.set(0, 6.2, 17.5);
camera.lookAt(0, 2.0, 0);

scene.add(new THREE.HemisphereLight(0xffffff, 0xe6f0ff, 0.75));
const sun = new THREE.DirectionalLight(0xffffff, 1.45);
sun.position.set(-4, 12, 7);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.radius = 6;
sun.shadow.bias = -0.0005;
Object.assign(sun.shadow.camera, { left: -11, right: 11, top: 8, bottom: -8, near: 1, far: 40 });
scene.add(sun);

// ---------- water: a soft pool that fades out at the edges ----------
const radial = (inner, outer, stops) => {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const g = c.getContext('2d'); const r = g.createRadialGradient(128, 128, inner, 128, 128, outer);
  for (const [o, col] of stops) r.addColorStop(o, col);
  g.fillStyle = r; g.fillRect(0, 0, 256, 256);
  return new THREE.CanvasTexture(c);
};
// ocean: a wavy blue surface that fades out softly at the edges (alpha uses the green channel)
const poolAlpha = (() => {
  const c = document.createElement('canvas'); c.width = 512; c.height = 256;
  const g = c.getContext('2d');
  const r = g.createRadialGradient(256, 128, 30, 256, 128, 250);
  r.addColorStop(0, '#fff'); r.addColorStop(0.55, '#fff'); r.addColorStop(0.85, '#777'); r.addColorStop(1, '#000');
  g.setTransform(1, 0, 0, 0.5, 0, 64);            // stretch into a wide ellipse
  g.fillStyle = r; g.fillRect(0, -128, 512, 512);
  return new THREE.CanvasTexture(c);
})();
const waterGeo = new THREE.PlaneGeometry(18.5, 10, 110, 60);
waterGeo.rotateX(-Math.PI / 2);
const waterBase = waterGeo.attributes.position.array.slice();
const water = new THREE.Mesh(waterGeo, new THREE.MeshPhysicalMaterial({
  color: 0x5aa5ff, roughness: 0.08, metalness: 0, clearcoat: 1, clearcoatRoughness: 0.05,
  transparent: true, opacity: 0.78, alphaMap: poolAlpha, depthWrite: false, sheen: 0.4, sheenColor: new THREE.Color(0xcfe6ff),
}));
water.position.set(0, 0, 0.3);
water.receiveShadow = true;
water.renderOrder = 2;
scene.add(water);
function updateWater(t) {
  const p = waterGeo.attributes.position.array;
  for (let i = 0; i < p.length; i += 3) {
    const x = waterBase[i], z = waterBase[i + 2];
    p[i + 1] = Math.sin(x * 0.9 + t * 1.3) * 0.06 + Math.sin(z * 1.6 - t * 1.0 + x * 0.3) * 0.045 + Math.sin((x + z) * 2.3 + t * 2.1) * 0.015;
  }
  waterGeo.attributes.position.needsUpdate = true;
  waterGeo.computeVertexNormals();
}

// splash rings
const ringTex = radial(96, 127, [[0, 'rgba(255,255,255,0)'], [0.5, 'rgba(255,255,255,0.9)'], [1, 'rgba(255,255,255,0)']]);
const rings = [];
function splash(x, z, size = 1) {
  for (let k = 0; k < 2; k++) {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: ringTex, transparent: true, depthWrite: false, color: 0xffffff }));
    m.rotation.x = -Math.PI / 2; m.position.set(x, 0.01, z); m.renderOrder = 3;
    scene.add(m); rings.push({ m, t0: clockT + k * 0.18, size });
  }
}
let clockT = 0;
function updateRings(t) {
  for (let i = rings.length - 1; i >= 0; i--) {
    const r = rings[i], k = (t - r.t0) / 1.4;
    if (k < 0) { r.m.visible = false; continue; }
    r.m.visible = true;
    if (k >= 1) { scene.remove(r.m); r.m.geometry.dispose(); r.m.material.dispose(); rings.splice(i, 1); continue; }
    const s = (0.4 + k * 2.4) * r.size; r.m.scale.set(s, s * 0.9, 1);
    r.m.material.opacity = (1 - k) * 0.85;
  }
}

const CLAY = new THREE.Color(0xf3f4f6);
const INK = new THREE.Color(0x1d1d22);
const WHITE = new THREE.Color(0xffffff);
const HAT_RED = new THREE.Color(0xee0000);
const clay = () => new THREE.MeshPhysicalMaterial({ color: CLAY.clone(), roughness: 0.36, clearcoat: 0.7, clearcoatRoughness: 0.2 });

// ---------- dolphin builder (faces +x, length ~2.7) ----------
const PROFILE = [[0, 0.04], [0.3, 0.17], [0.8, 0.4], [1.3, 0.56], [1.7, 0.6], [2.0, 0.54], [2.2, 0.42], [2.32, 0.26], [2.45, 0.17], [2.6, 0.13], [2.7, 0.07], [2.73, 0]];
function makeDolphin(o) {
  const d = new THREE.Group();                 // position + heading
  const pitch = new THREE.Group(); d.add(pitch); // nose up/down while jumping
  const roll = new THREE.Group(); pitch.add(roll);
  const mat = clay();
  const eyeMat = new THREE.MeshPhysicalMaterial({ color: INK.clone(), roughness: 0.2, clearcoat: 1 });
  const add = (m, parent = roll) => { m.castShadow = true; parent.add(m); return m; };
  const fat = o.fat ?? 1;

  const pts = PROFILE.map(([u, r]) => new THREE.Vector2(r * fat, u));
  const geo = new THREE.LatheGeometry(pts, 48);
  geo.rotateZ(-Math.PI / 2); geo.translate(-1.35, 0, 0); geo.scale(1, 1, 0.9);
  const body = add(new THREE.Mesh(geo, mat));

  // dorsal fin
  const fin = new THREE.Shape();
  fin.moveTo(0.22, 0); fin.quadraticCurveTo(0.12, 0.42, -0.26, 0.56); fin.quadraticCurveTo(-0.16, 0.22, -0.4, 0); fin.closePath();
  const finGeo = new THREE.ExtrudeGeometry(fin, { depth: 0.06, bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.03, bevelSegments: 4 });
  finGeo.translate(0, 0, -0.03);
  const dorsal = add(new THREE.Mesh(finGeo, mat)); dorsal.position.set(-0.3, 0.48 * fat, 0); dorsal.scale.setScalar(0.85);

  // pectoral fins
  for (const s of [-1, 1]) {
    const p = add(new THREE.Mesh(new THREE.SphereGeometry(1, 24, 12), mat));
    p.scale.set(0.3, 0.05, 0.13); p.position.set(0.45, -0.3 * fat, s * 0.44 * fat); p.rotation.set(s * 0.5, s * 0.5, -0.35);
  }
  // tail flukes on a pivot so they can beat
  const tail = new THREE.Group(); tail.position.set(-1.33, 0, 0); roll.add(tail);
  for (const s of [-1, 1]) {
    const f = add(new THREE.Mesh(new THREE.SphereGeometry(1, 24, 12), mat), tail);
    f.scale.set(0.24, 0.045, 0.2); f.position.set(-0.12, 0, s * 0.2); f.rotation.y = s * 0.7;
  }
  // eyes on both sides of the head (raycast onto the body so they sit on the surface)
  body.updateMatrixWorld(true);
  const ray = new THREE.Raycaster();
  const eyes = [];
  for (const s of [-1, 1]) {
    ray.set(new THREE.Vector3(0.72, 0.2 * fat, s * 5), new THREE.Vector3(0, 0, -s));
    const h = ray.intersectObject(body, false)[0]; if (!h) continue;
    const holder = new THREE.Group(); holder.position.copy(h.point).addScaledVector(h.face.normal, 0.01);
    holder.lookAt(h.point.clone().add(h.face.normal));
    const sleep = new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.022, 10, 24, Math.PI), new THREE.MeshPhysicalMaterial({ color: INK, roughness: 0.3 }));
    sleep.rotation.z = Math.PI;
    const open = new THREE.Mesh(new THREE.SphereGeometry(0.12, 32, 20), eyeMat);
    open.scale.set(0.85, 0.0001, 0.32);
    holder.add(sleep, open); roll.add(holder);
    eyes.push({ sleep, open });
  }
  // little smile line on the beak
  for (const s of [-1, 1]) {
    ray.set(new THREE.Vector3(1.0, -0.05, s * 5), new THREE.Vector3(0, 0, -s));
    const h = ray.intersectObject(body, false)[0]; if (!h) continue;
    const sm = new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.014, 8, 16, Math.PI * 0.7), new THREE.MeshPhysicalMaterial({ color: INK, roughness: 0.4 }));
    const hd = new THREE.Group(); hd.position.copy(h.point).addScaledVector(h.face.normal, 0.008); hd.lookAt(h.point.clone().add(h.face.normal));
    sm.rotation.z = Math.PI * 1.15; hd.add(sm); roll.add(hd);
  }
  // a little red fedora for some of the pod
  let hatMat = null;
  if (o.hat) {
    hatMat = new THREE.MeshPhysicalMaterial({ color: CLAY.clone(), roughness: 0.55, sheen: 0.6, sheenColor: new THREE.Color(0xffd0d0) });
    const bandMat = new THREE.MeshStandardMaterial({ color: 0x0b0b0b, roughness: 0.9 });
    ray.set(new THREE.Vector3(0.38, 5, 0), new THREE.Vector3(0, -1, 0));
    const top = ray.intersectObject(body, false)[0];
    const hat = new THREE.Group();
    if (top) {
      const n = top.face.normal.clone().normalize();
      hat.position.copy(top.point).addScaledVector(n, -0.05);          // sink the brim slightly into the head
      hat.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), n);
      hat.rotateX(0.08);                                               // a jaunty tilt
    } else hat.position.set(0.38, 0.5, 0);
    hat.scale.setScalar(1.12);
    const brim = add(new THREE.Mesh(new THREE.CylinderGeometry(0.46, 0.46, 0.035, 40), hatMat), hat);
    brim.scale.set(1, 1, 0.85);
    const crownPts = [[0, 0], [0.27, 0], [0.27, 0.05], [0.25, 0.22], [0.2, 0.3], [0.08, 0.27], [0, 0.29]].map(([r, y]) => new THREE.Vector2(r, y));
    const crown = add(new THREE.Mesh(new THREE.LatheGeometry(crownPts, 40), hatMat), hat);
    crown.scale.set(1, 1, 0.82); crown.position.y = 0.015;
    // Keep the ribbon just outside the crown so it reads clearly above the brim.
    const band = add(new THREE.Mesh(new THREE.CylinderGeometry(0.273, 0.28, 0.11, 40, 1, true), bandMat), hat);
    band.scale.set(1, 1, 0.82); band.position.y = 0.074;
    roll.add(hat);
  }
  Object.assign(d.userData, { pitch, roll, tail, mat, eyeMat, eyes, hatMat });
  return d;
}

// ---------- the pod: different sizes and blues ----------
const pod = [
  { x: -5.6, z: 0.6,  s: 0.62, color: 0x1f6feb, h: 0.2, hat: true },
  { x: -3.0, z: 2.2,  s: 0.42, color: 0x5ab0ff, h: 2.6, fat: 1.1 },
  { x: -6.2, z: -1.4, s: 0.5,  color: 0x0b3d91, h: -0.4, hat: true },
  { x: -1.0, z: -0.6, s: 0.55, color: 0x2f80ff, h: 3.4 },
  { x: 1.2,  z: 2.4,  s: 0.36, color: 0x8cc8ff, h: 0.8, fat: 1.15, hat: true },
  { x: 3.0,  z: 0.2,  s: 0.66, color: 0x0a58ca, h: 2.9 },
  { x: 5.4,  z: 1.8,  s: 0.44, color: 0x38a3ff, h: 3.6, hat: true },
  { x: 6.0,  z: -1.2, s: 0.52, color: 0x15489e, h: 2.2 },
];

let seed = 11; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
const dolphins = pod.map((c, i) => {
  const m = makeDolphin(c);
  m.scale.setScalar(c.s);
  scene.add(m);
  m.userData.cfg = { ...c, target: new THREE.Color(c.color), i };
  return m;
});

const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
const BOUNDS = { x: 6.2, z0: -2.0, z1: 2.6 };

function resetPod() {
  seed = 11;
  for (const r of rings.splice(0)) { scene.remove(r.m); r.m.geometry.dispose(); r.m.material.dispose(); }
  dolphins.forEach((m, i) => {
    const c = m.userData.cfg;
    const order = [3, 0, 6, 1, 7, 2, 5, 4][i] ?? i;
    Object.assign(m.userData, { x: c.x, z: c.z, h: c.h, wake: 1.4 + order * 0.45, speed: 0, turn: 0, jump: null,
      nextJump: 0, last: undefined, wander: 0, wokeSplash: false, p: 0, r: 0, ph: 0 });
    m.userData.nextJump = m.userData.wake + 4 + rnd() * 4;
  });
}

function blinkAmount(a, wake) {
  const pulse = (center, w) => Math.max(0, 1 - Math.abs(a - center) / w);
  let b = Math.max(pulse(0.55, 0.12), pulse(0.95, 0.1));
  if (a > 1.6) { const per = 2.6 + (wake * 7.3) % 1.2; const p = (a + wake * 3.1) % per; b = Math.max(b, Math.max(0, 1 - Math.abs(p - 0.1) / 0.09)); }
  return b;
}

function update(t) {
  clockT = t;
  for (const m of dolphins) {
    const u = m.userData, c = u.cfg;
    const dt = Math.min(t - (u.last ?? t), 0.05); u.last = t;
    const a = t - u.wake;
    const opened = ease(a / 0.35), blink = a > 0 ? blinkAmount(a, u.wake) : 0;
    for (const e of u.eyes) { e.sleep.scale.setScalar(Math.max(1 - opened, 0.0001)); e.open.scale.y = Math.max(0.78 * opened * (1 - blink), 0.0001); }
    const col = ease((a - 1.15) / 1.3);
    u.mat.color.copy(CLAY).lerp(c.target, col);
    // eyes stay black and glossy (they read better than white on blue)
    if (u.hatMat) u.hatMat.color.copy(CLAY).lerp(HAT_RED, col);

    let y = 0.02, pitchT = 0, rollT = 0;
    if (a < 0) { y += Math.sin(t * 1.6 + c.i) * 0.04; rollT = Math.sin(t * 1.1 + c.i) * 0.06; }
    else if (a < 0.9) {
      const k = Math.sin(Math.min(a / 0.9, 1) * Math.PI);           // a soft stretch as it wakes
      y += k * 0.22; pitchT = k * 0.18;
      if (a > 0.75 && !u.wokeSplash) { splash(u.x, u.z, 0.6); u.wokeSplash = true; }
    }

    // ---- swimming: smooth wander + soft steering forces, eased turning ----
    const swim = ease((a - 2.4) / 1.6);                                 // 0 -> 1 as it starts swimming
    u.speed += (1.65 * swim - u.speed) * Math.min(dt * 1.5, 1);
    if (swim > 0) {
      // desired direction = current heading + slow wander + gentle push away from edges and neighbours
      const wander = Math.sin(t * 0.37 + c.i * 1.7) * 0.6 + Math.sin(t * 0.21 + c.i * 0.9) * 0.4;
      let dx = Math.cos(u.h + wander * 0.6), dz = Math.sin(u.h + wander * 0.6);
      const lx = BOUNDS.x - Math.max(u.z, 0) * 0.5;
      const edge = (v, lo, hi, m) => (v < lo + m ? (lo + m - v) / m : 0) - (v > hi - m ? (v - (hi - m)) / m : 0);
      dx += edge(u.x, -lx, lx, 1.8) * 2.2;
      dz += edge(u.z, BOUNDS.z0, BOUNDS.z1, 1.0) * 2.2;
      for (const o of dolphins) if (o !== m) {
        const ox = u.x - o.userData.x, oz = u.z - o.userData.z, dd = Math.hypot(ox, oz);
        if (dd < 1.8 && dd > 0.001) { const f = (1.8 - dd) / 1.8; dx += ox / dd * f * 1.5; dz += oz / dd * f * 1.5; }
      }
      let want = Math.atan2(dz, dx) - u.h;
      want = Math.atan2(Math.sin(want), Math.cos(want));                  // shortest way round
      const targetTurn = Math.max(-1.1, Math.min(1.1, want * 1.6));        // rad/s, capped
      u.turn += (targetTurn - u.turn) * Math.min(dt * 1.8, 1);            // eased: no snapping
      u.h += u.turn * dt;
      u.x += Math.cos(u.h) * u.speed * dt; u.z += Math.sin(u.h) * u.speed * dt;
      rollT = -u.turn * 0.32;                                               // bank into turns

      // leaps: eased take-off and landing, pitch follows the arc
      if (!u.jump && t >= u.nextJump && swim >= 1) { u.jump = { t0: t, dur: 1.35, hgt: 1.3 + rnd() * 0.7 }; splash(u.x, u.z, 0.8); }
      const swimBob = Math.sin(t * 2.0 + c.i) * 0.05;
      if (u.jump) {
        const q = (t - u.jump.t0) / u.jump.dur;
        if (q >= 1) { splash(u.x, u.z, 1.1); u.jump = null; u.nextJump = t + 4 + rnd() * 5; }
        else {
          const arc = Math.sin(q * Math.PI);                              // smooth up and down
          y = 0.02 + swimBob * (1 - arc) + arc * u.jump.hgt;
          pitchT = Math.cos(q * Math.PI) * 0.75;                           // nose up, level, nose down
          u.speed = Math.max(u.speed, 1.35 + arc * 0.5);
        }
      } else {
        y += swimBob * swim;
        pitchT = Math.cos(t * 2.0 + c.i) * 0.09 * swim;                    // porpoising
      }
    }

    // everything eases toward its target, so motion stays fluid
    u.p = (u.p ?? 0) + (pitchT - (u.p ?? 0)) * Math.min(dt * 6, 1);
    u.r = (u.r ?? 0) + (rollT - (u.r ?? 0)) * Math.min(dt * 3, 1);
    m.position.set(u.x, y * c.s * 1.6, u.z);
    m.rotation.y = -u.h;
    u.pitch.rotation.z = u.p;
    u.roll.rotation.x = u.r;
    // tail beat: frequency and strength blend smoothly with swimming speed
    u.ph = (u.ph ?? 0) + dt * (2 + 5 * swim);
    const beat = 0.12 + 0.26 * swim + (u.jump ? 0.08 : 0);
    u.tail.rotation.z = Math.sin(u.ph + c.i) * beat;
    u.roll.rotation.z = Math.sin(u.ph + c.i - 0.9) * 0.04 * swim;           // gentle body wave ahead of the tail
  }
  updateRings(t);
  updateWater(t);
}

let t0 = performance.now(), wasOn = false, paused = false;
function frame(now) {
  requestAnimationFrame(frame);
  const on = slide.classList.contains('on');
  if (on && !wasOn) {
    t0 = now; resetPod();
    slide.classList.remove('play'); void slide.offsetWidth; slide.classList.add('play');
  }
  wasOn = on;
  if (!on || paused) return;
  update(STILL ? 9 : (now - t0) / 1000);
  renderer.render(scene, camera);
}
resetPod();
requestAnimationFrame(frame);
window.__crabs = {
  count: dolphins.length,
  sim(sec) { resetPod(); for (let t = 0; t <= sec; t += 1 / 60) update(t); renderer.render(scene, camera); paused = true; },
  play() { paused = false; },
};
