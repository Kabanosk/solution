// Crably title animation.
// Small clay crabs in different toy shapes (rounded cube, hexagon, pill, capsule,
// gumdrop, blob) sit asleep on an invisible floor. One by one they blink awake,
// colour fades in (each a different red), then they hop around gently.
// Restarts every time slide 1 becomes active.
import * as THREE from 'three';
import { RoomEnvironment } from './vendor/RoomEnvironment.js';
import { RoundedBoxGeometry } from './vendor/RoundedBoxGeometry.js';

const canvas = document.getElementById('crabs');
const slide = canvas.closest('.slide');
const W = 1600, H = 900;
const STILL = !!document.documentElement.dataset.embed || matchMedia('(prefers-reduced-motion: reduce)').matches;

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

scene.add(new THREE.HemisphereLight(0xffffff, 0xeeeae8, 0.7));
const sun = new THREE.DirectionalLight(0xffffff, 1.5);
sun.position.set(-4, 12, 7);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.radius = 6;
sun.shadow.bias = -0.0005;
Object.assign(sun.shadow.camera, { left: -11, right: 11, top: 8, bottom: -8, near: 1, far: 40 });
scene.add(sun);

const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 20), new THREE.ShadowMaterial({ opacity: 0.1 }));
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);

const contactTex = (() => {
  const c = document.createElement('canvas'); c.width = c.height = 128;
  const g = c.getContext('2d'); const r = g.createRadialGradient(64, 64, 2, 64, 64, 62);
  r.addColorStop(0, 'rgba(60,20,20,0.35)'); r.addColorStop(0.5, 'rgba(60,20,20,0.12)'); r.addColorStop(1, 'rgba(60,20,20,0)');
  g.fillStyle = r; g.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
})();

const CLAY = new THREE.Color(0xf3f1ef);
const INK = new THREE.Color(0x1d1d22);
const WHITE = new THREE.Color(0xffffff);
const clay = () => new THREE.MeshPhysicalMaterial({ color: CLAY.clone(), roughness: 0.4, clearcoat: 0.55, clearcoatRoughness: 0.28 });

// ---------- body shapes (all sit with their bottom at y = 0) ----------
function hexGeometry(r, h, bevel) {
  const shape = new THREE.Shape();
  for (let k = 0; k < 6; k++) {
    const a = Math.PI / 6 + k * Math.PI / 3;
    const p = [Math.cos(a) * r, Math.sin(a) * r];
    k ? shape.lineTo(...p) : shape.moveTo(...p);
  }
  shape.closePath();
  const g = new THREE.ExtrudeGeometry(shape, { depth: h, bevelEnabled: true, bevelThickness: bevel, bevelSize: bevel, bevelSegments: 8, curveSegments: 4 });
  g.rotateX(-Math.PI / 2); g.center();
  return g;
}
function domeGeometry(r, h) {
  // gumdrop: flat-ish bottom, rounded top
  const pts = [];
  for (let i = 0; i <= 24; i++) {
    const t = i / 24, a = t * Math.PI / 2;
    pts.push(new THREE.Vector2(Math.cos(a) * r, Math.sin(a) * h));
  }
  pts.unshift(new THREE.Vector2(0, 0), new THREE.Vector2(r * 0.92, 0), new THREE.Vector2(r, h * 0.06));
  const g = new THREE.LatheGeometry(pts, 48); g.center();
  return g;
}
const SHAPES = {
  cube: () => new RoundedBoxGeometry(1.9, 1.5, 1.6, 8, 0.42),
  hex: () => hexGeometry(1.05, 1.1, 0.28),
  pill: () => { const g = new THREE.CapsuleGeometry(0.62, 1.3, 12, 32); g.rotateZ(Math.PI / 2); return g; },
  tall: () => new THREE.CapsuleGeometry(0.7, 0.85, 12, 32),
  dome: () => domeGeometry(1.05, 1.25),
  blob: () => { const g = new THREE.SphereGeometry(1, 48, 32); g.scale(1.05, 0.88, 0.95); return g; },
};

const ray = new THREE.Raycaster();
const hitOn = (mesh, from, dir) => { ray.set(from, dir); const h = ray.intersectObject(mesh, false)[0]; return h; };

// ---------- crab builder ----------
function makeCrab(o) {
  const crab = new THREE.Group();
  const body = new THREE.Group(); crab.add(body);
  const mat = clay();
  const eyeMat = new THREE.MeshPhysicalMaterial({ color: INK.clone(), roughness: 0.2, clearcoat: 1 });
  const add = (m, parent = body) => { m.castShadow = true; parent.add(m); return m; };

  const geo = SHAPES[o.shape]();
  geo.scale(o.thin ?? 0.8, 1, (o.thin ?? 0.8) * 0.95);
  geo.computeBoundingBox();
  const bb = geo.boundingBox, hx = (bb.max.x - bb.min.x) / 2, hy = (bb.max.y - bb.min.y) / 2, hz = (bb.max.z - bb.min.z) / 2;
  const lift = hy + 0.26;                                   // legs underneath
  const shell = add(new THREE.Mesh(geo, mat));
  shell.position.y = lift;
  if (o.tilt) shell.rotation.y = o.tilt;
  shell.updateMatrixWorld(true);

  // place features on the actual surface
  const onFront = (x, y) => hitOn(shell, new THREE.Vector3(x, lift + y, 10), new THREE.Vector3(0, 0, -1));
  const onTop = (x, z) => hitOn(shell, new THREE.Vector3(x, 20, z), new THREE.Vector3(0, -1, 0));

  const eyes = [];
  for (const s of [-1, 1]) {
    const h = onFront(s * hx * 0.32, hy * 0.12); if (!h) continue;
    const holder = new THREE.Group(); holder.position.copy(h.point).addScaledVector(h.face.normal, 0.01);
    holder.lookAt(h.point.clone().add(h.face.normal));
    const sleep = new THREE.Mesh(new THREE.TorusGeometry(0.11, 0.024, 10, 24, Math.PI), new THREE.MeshPhysicalMaterial({ color: INK, roughness: 0.3 }));
    sleep.rotation.z = Math.PI;
    const open = new THREE.Mesh(new THREE.SphereGeometry(0.15, 32, 20), eyeMat);
    open.scale.set(0.85, 0.0001, 0.3);
    holder.add(sleep, open); body.add(holder);
    eyes.push({ sleep, open });
  }
  // one little antenna-stalk pair on top (like the zgrai bots)
  for (const s of [-1, 1]) {
    const h = onTop(s * hx * 0.26, 0); if (!h) continue;
    const stalk = add(new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.035, 0.32, 12), mat));
    stalk.position.copy(h.point).add(new THREE.Vector3(s * 0.03, 0.14, 0)); stalk.rotation.z = -s * 0.22;
    const tip = add(new THREE.Mesh(new THREE.SphereGeometry(0.075, 20, 14), mat));
    tip.position.copy(h.point).add(new THREE.Vector3(s * 0.07, 0.31, 0));
  }
  // claws
  const claws = [];
  for (const s of [-1, 1]) {
    const arm = add(new THREE.Mesh(new THREE.CapsuleGeometry(0.075, 0.3, 6, 12), mat));
    arm.position.set(s * (hx + 0.08), lift - hy * 0.1, hz * 0.3); arm.rotation.set(-0.4, 0, s * 1.0);
    const claw = new THREE.Group(); claw.position.set(s * (hx + 0.32), lift + hy * 0.12, hz * 0.5); claw.rotation.y = -s * 0.5; body.add(claw);
    const palm = add(new THREE.Mesh(new THREE.SphereGeometry(0.24, 32, 20), mat), claw); palm.scale.set(1.05, 0.85, 0.9);
    const upper = new THREE.Group(); upper.position.set(0, 0.08, 0.14); claw.add(upper);
    const uf = add(new THREE.Mesh(new THREE.SphereGeometry(0.14, 24, 16), mat), upper); uf.scale.set(0.7, 0.55, 1.35); uf.position.z = 0.13;
    const lower = new THREE.Group(); lower.position.set(0, -0.065, 0.14); claw.add(lower);
    const lf = add(new THREE.Mesh(new THREE.SphereGeometry(0.12, 24, 16), mat), lower); lf.scale.set(0.7, 0.5, 1.2); lf.position.z = 0.1;
    claws.push({ upper, lower, s });
  }
  // legs
  const legs = [];
  for (const s of [-1, 1]) for (const [i, z] of [[0, -0.45], [1, -0.08], [2, 0.3]]) {
    const pivot = new THREE.Group(); pivot.position.set(s * hx * 0.7, lift - hy * 0.75, z * hz); body.add(pivot);
    const leg = add(new THREE.Mesh(new THREE.CapsuleGeometry(0.055, 0.34, 6, 10), mat), pivot);
    leg.position.set(s * 0.15, -0.12, 0); leg.rotation.z = s * 1.0;
    legs.push({ pivot, s, i });
  }
  const contact = new THREE.Mesh(new THREE.PlaneGeometry(hx * 3.6, hz * 3.2), new THREE.MeshBasicMaterial({ map: contactTex, transparent: true, depthWrite: false }));
  contact.rotation.x = -Math.PI / 2; contact.position.y = 0.01;
  Object.assign(crab.userData, { body, mat, eyeMat, eyes, claws, legs, contact });
  return crab;
}

// ---------- the crew: different shapes, every crab a different red ----------
const crew = [
  { shape: 'cube', x: -6.4, z: 0.4,  s: 0.62, color: 0xe5484d, tilt: 0.15 },
  { shape: 'pill', x: -4.2, z: 2.4,  s: 0.38, color: 0xff7b72 },
  { shape: 'hex',  x: -6.6, z: -1.8, s: 0.5,  color: 0x9b1c24 },
  { shape: 'tall', x: -2.5, z: 0.0,  s: 0.55, color: 0xee0000, thin: 0.75 },
  { shape: 'blob', x: -0.3, z: 2.8,  s: 0.3,  color: 0xff9a8b },
  { shape: 'dome', x: 1.9,  z: 0.8,  s: 0.66, color: 0xc4161c },
  { shape: 'cube', x: 4.2,  z: 2.4,  s: 0.34, color: 0xf2555a, tilt: -0.2 },
  { shape: 'pill', x: 5.9,  z: 0.1,  s: 0.52, color: 0x7a0f17 },
  { shape: 'hex',  x: 6.5,  z: -1.9, s: 0.42, color: 0xff5a4e },
  { shape: 'tall', x: 3.6,  z: -1.7, s: 0.36, color: 0xd93f3f, thin: 0.75 },
];

let seed = 7; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);

const crabs = crew.map((c, i) => {
  const m = makeCrab(c);
  m.scale.setScalar(c.s);
  scene.add(m); scene.add(m.userData.contact);
  m.userData.cfg = { ...c, target: new THREE.Color(c.color), i };
  return m;
});

// ---------- timeline ----------
const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));

function resetCrew() {
  seed = 7;
  crabs.forEach((m, i) => {
    const c = m.userData.cfg;
    const order = [4, 1, 7, 2, 9, 0, 5, 8, 3, 6][i] ?? i;
    Object.assign(m.userData, { x: c.x, z: c.z, wake: 1.4 + order * 0.42, face: (rnd() - 0.5) * 0.4,
      act: null, stride: 0, last: undefined, turn: undefined, dir: 0 });
    for (const l of m.userData.legs) l.w = 0;
  });
}

// next spot to stroll to: mostly sideways, on screen, not on top of another crab
const BOUNDS = { x0: -6.8, x1: 6.8, z0: -2.0, z1: 2.8 };
function pickTarget(me) {
  const u = me.userData;
  let best = null, bestScore = -1;
  for (let k = 0; k < 8; k++) {
    const dist = 1.0 + rnd() * 2.6;
    const ang = (rnd() - 0.5) * 1.1 + (rnd() < 0.5 ? 0 : Math.PI);      // mostly left/right
    const z = Math.min(Math.max(u.z + Math.sin(ang) * dist * 0.6, BOUNDS.z0), BOUNDS.z1);
    const xl = BOUNDS.x1 - Math.max(z, 0) * 0.5;              // the screen is narrower at the front
    const x = Math.min(Math.max(u.x + Math.cos(ang) * dist, -xl), xl);
    let clear = 99;
    for (const o of crabs) if (o !== me) {
      const ou = o.userData, ox = ou.act?.kind === 'walk' ? ou.act.tx : ou.x, oz = ou.act?.kind === 'walk' ? ou.act.tz : ou.z;
      clear = Math.min(clear, Math.hypot(ox - x, oz - z));
    }
    if (clear > bestScore) { bestScore = clear; best = { x, z }; }
    if (clear > 1.6) break;
  }
  return best;
}

function blinkAmount(a, wake) {
  const pulse = (center, w) => Math.max(0, 1 - Math.abs(a - center) / w);
  let b = Math.max(pulse(0.55, 0.12), pulse(0.95, 0.1));
  if (a > 2) { const p = (a + wake * 3.1) % 4.3; b = Math.max(b, Math.max(0, 1 - Math.abs(p - 0.1) / 0.09)); }
  return b;
}

function update(t) {
  for (const m of crabs) {
    const u = m.userData, c = u.cfg;
    const a = t - u.wake;
    const opened = ease(a / 0.35);
    const blink = a > 0 ? blinkAmount(a, u.wake) : 0;
    for (const e of u.eyes) {
      e.sleep.scale.setScalar(Math.max(1 - opened, 0.0001));
      e.open.scale.y = Math.max(0.7 * opened * (1 - blink), 0.0001);
    }
    const col = ease((a - 1.15) / 1.3);
    u.mat.color.copy(CLAY).lerp(c.target, col);
    u.eyeMat.color.copy(INK).lerp(WHITE, col);

    let sy = 1, sxz = 1, y = 0, roll = 0;
    if (a < 0) { const br = Math.sin(t * 2 + c.i) * 0.025; sy = 1 + br; sxz = 1 - br * 0.5; }
    else if (a < 0.6) { const k = Math.sin(Math.min(a / 0.6, 1) * Math.PI); sy = 1 + k * 0.1; sxz = 1 - k * 0.05; y = k * 0.15; }

    // behaviour once awake and coloured: idle -> walk (on legs) -> idle ..., sometimes a hop
    const dt = Math.min(t - (u.last ?? t), 0.05); u.last = t;
    let walking = false;
    if (a > 2.6) {
      if (!u.act) u.act = { kind: 'idle', until: t + 0.3 + rnd() * 1.2 };
      const act = u.act;
      if (act.kind === 'idle' && t >= act.until) {
        if (rnd() < 0.18) u.act = { kind: 'hop', t0: t, dur: 0.6 };
        else { const p = pickTarget(m); u.act = { kind: 'walk', tx: p.x, tz: p.z, speed: 0.9 + rnd() * 0.5 }; }
      }
      if (u.act.kind === 'walk') {
        const dx = u.act.tx - u.x, dz = u.act.tz - u.z, d = Math.hypot(dx, dz);
        if (d < 0.05) u.act = { kind: 'idle', until: t + 0.8 + rnd() * 1.8, look: (rnd() - 0.5) * 0.8 };
        else {
          const step = Math.min(d, u.act.speed * dt * (0.4 + 0.6 * ease(Math.min(d, 1))));  // slow down on arrival
          u.x += dx / d * step; u.z += dz / d * step; u.stride = (u.stride ?? 0) + step;
          u.dir = dx / d; walking = true;
        }
      }
      if (u.act.kind === 'hop') {
        const k = (t - u.act.t0) / u.act.dur;
        if (k < 0.2) { const q = Math.sin(k / 0.2 * Math.PI / 2); sy = 1 - 0.15 * q; sxz = 1 + 0.07 * q; }
        else if (k < 1) { const q = (k - 0.2) / 0.8; y = Math.sin(q * Math.PI) * 0.9; sy = 1 + 0.1 * Math.sin(q * Math.PI); }
        else if (k < 1.25) { const q = Math.sin((k - 1) / 0.25 * Math.PI); sy = 1 - 0.12 * q; sxz = 1 + 0.06 * q; }
        else u.act = { kind: 'idle', until: t + 0.6 + rnd() * 1.2 };
      }
    }
    const ph = (u.stride ?? 0) * 7.5;                     // gait phase follows distance walked
    if (walking) { y += Math.abs(Math.sin(ph)) * 0.07; roll = Math.sin(ph) * 0.05; }

    m.position.set(u.x, y * c.s, u.z);
    u.body.scale.set(sxz, sy, sxz);
    u.body.rotation.z = roll;
    // crabs walk sideways: mostly face the camera, lean a little into the direction of travel
    const want = u.face + (walking ? (u.dir ?? 0) * 0.35 : (u.act?.look ?? 0));
    u.turn = (u.turn ?? want) + (want - (u.turn ?? want)) * Math.min(dt * 4, 1);
    m.rotation.y = u.turn + Math.sin(t * 0.7 + c.i) * 0.04;
    u.contact.position.set(u.x, 0.01, u.z);
    const hs = 1 - Math.min(y, 1) * 0.45;
    u.contact.scale.setScalar(c.s * hs); u.contact.material.opacity = 0.9 * hs;
    for (const cl of u.claws) {
      const open = a > 0 ? (Math.sin(t * 2.4 + c.i + cl.s) * 0.5 + 0.5) * 0.4 : 0.05;
      cl.upper.rotation.x = -open; cl.lower.rotation.x = open * 0.6;
      cl.upper.parent.rotation.x = walking ? Math.sin(ph + cl.s) * 0.15 : 0;
    }
    // alternating tripod gait: three legs lift and swing while the other three push
    for (const l of u.legs) {
      const grp = (l.i + (l.s > 0 ? 1 : 0)) % 2;
      const w = walking ? 1 : 0;
      l.w = (l.w ?? 0) + (w - (l.w ?? 0)) * Math.min(dt * 8, 1);
      const sw = Math.sin(ph + grp * Math.PI);
      l.pivot.rotation.z = l.s * Math.max(sw, 0) * 0.42 * l.w + (y > 0.2 ? l.s * 0.2 : 0);
      l.pivot.rotation.y = Math.cos(ph + grp * Math.PI) * 0.4 * l.w;
    }
  }
}

let t0 = performance.now(), wasOn = false, paused = false;
function frame(now) {
  requestAnimationFrame(frame);
  const on = slide.classList.contains('on');
  if (on && !wasOn) {
    t0 = now; resetCrew();
    slide.classList.remove('play'); void slide.offsetWidth; slide.classList.add('play');
  }
  wasOn = on;
  if (!on || paused) return;
  update(STILL ? 9 : (now - t0) / 1000);
  renderer.render(scene, camera);
}
resetCrew();
requestAnimationFrame(frame);
// debug: fast-forward the timeline (used to check the walk in a throttled preview)
window.__crabs = {
  count: crabs.length,
  sim(sec) { resetCrew(); for (let t = 0; t <= sec; t += 1 / 60) update(t); renderer.render(scene, camera); paused = true; },
  play() { paused = false; },
};
