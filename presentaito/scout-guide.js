// The red-hat frog from the opener guides the walkthrough. Each scene is
// authored on the product UI in stage pixels: hops land next to evidence and,
// on the quieter slides, the frog hides behind a panel with its hat showing.
import * as THREE from 'three';
import { makeFrogModel, setEyes } from './frog-model.js';

const stage = document.getElementById('stage');
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const still = () => reduced.matches || Boolean(document.documentElement.dataset.embed);

const canvas = document.createElement('canvas');
canvas.className = 'guide-canvas';
canvas.setAttribute('aria-hidden', 'true');
canvas.hidden = true;
stage.append(canvas);
const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.setSize(1600, 900, false);
renderer.toneMapping = THREE.NeutralToneMapping;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;

// Orthographic view in stage pixels, pitched like the opener so the ground
// shadow and the top of the hat both read.
const pitch = .42, cosP = Math.cos(pitch);
const scene = new THREE.Scene();
const camera = new THREE.OrthographicCamera(-800, 800, 450, -450, 1, 9000);
camera.position.set(0, 4000 * Math.sin(pitch), 4000 * cosP);
camera.lookAt(0, 0, 0);
camera.updateMatrixWorld();
scene.add(new THREE.HemisphereLight(0xfbfff2, 0x869170, 1.75));
const key = new THREE.DirectionalLight(0xfffcee, 2.0);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.bias = -.0006;
key.shadow.radius = 3.25;
Object.assign(key.shadow.camera, { left: -520, right: 520, top: 420, bottom: -420, near: 1, far: 3000 });
scene.add(key, key.target);
const rim = new THREE.DirectionalLight(0xe8ffc5, 1.1);
rim.position.set(160, 200, -200);
scene.add(rim);
const floor = new THREE.Mesh(new THREE.PlaneGeometry(6000, 6000), new THREE.ShadowMaterial({ color: '#202820', opacity: .13, depthWrite: false }));
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);

const ray = new THREE.Raycaster();
const ground = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
const ndc = (x, y) => new THREE.Vector2(x / 800 - 1, 1 - y / 450);
function groundAt(x, y) {
  ray.setFromCamera(ndc(x, y), camera);
  return ray.ray.intersectPlane(ground, new THREE.Vector3());
}
const toStage = v => ({ x: (v.x + 1) * 800, y: (1 - v.y) * 450 });

const smooth = x => { x = THREE.MathUtils.clamp(x, 0, 1); return x*x*(3-2*x); };
const shortestTurn = (from, to) => Math.atan2(Math.sin(to - from), Math.cos(to - from));
const pulse = (t, start, width) => smooth((t - start) / (width*.45)) * (1 - smooth((t - start - width*.45) / (width*.55)));
const blink = (t, phase, period) => 1 - .96 * pulse(((t + phase) % period + period) % period, period * .55, .19);

function actor(hue, hat) {
  const a = makeFrogModel({ hue, hat });
  a.root.visible = a.shadow.visible = false;
  scene.add(a.root, a.shadow);
  return a;
}
const guide = actor('#71964b', true);
const companions = [actor('#62a063'), actor('#86a54d'), actor('#4b8755')];
const hatTop = 1.68, size = 38;

// Place a frog: foot on the ground at stage point (x, y), lifted `lift` screen
// pixels, facing `yaw` (0 looks at the audience).
function place(a, x, y, lift, yaw, scale, squash = 0, stretch = 0, t = 0) {
  const p = groundAt(x, y);
  const up = lift / cosP;
  a.root.position.set(p.x, up, p.z);
  a.root.rotation.y = yaw;
  a.root.scale.setScalar(scale);
  a.upper.position.set(0, 0, 0);
  a.upper.scale.set(1 + squash*.36, 1 - squash + stretch + Math.sin(t*2.1) * .019, 1 + squash*.2);
  const airborne = Math.min(1, up / 40);
  a.shadow.position.set(p.x, .5, p.z);
  a.shadow.rotation.y = yaw;
  a.shadow.scale.set(scale * (3.2 + airborne * .6), scale * (2.65 + airborne * .6), 1);
  a.shadow.material.opacity = .74 - .55 * airborne;
  a.root.visible = a.shadow.visible = true;
}

const turnLead = .42, pause = .18;
// No single hop is longer than a frog or two: long legs become several short hops.
const maxHop = 170;
function buildHops(route, face) {
  const hops = [];
  let clock = pause, previous = face;
  for (let i = 1; i < route.length; i++) {
    const [x0, y0] = route[i-1];
    const [x1, y1, lift, duration] = route[i];
    const parts = Math.max(1, Math.ceil(Math.hypot(x1 - x0, (y1 - y0) / cosP) / maxHop));
    const heading = Math.atan2(x1 - x0, (y1 - y0) / cosP);
    for (let k = 1; k <= parts; k++) {
      const from = [THREE.MathUtils.lerp(x0, x1, (k-1)/parts), THREE.MathUtils.lerp(y0, y1, (k-1)/parts)];
      const to = [THREE.MathUtils.lerp(x0, x1, k/parts), THREE.MathUtils.lerp(y0, y1, k/parts)];
      const last = k === parts;
      const d = last ? Math.min(duration, .56) : .46;
      hops.push({ from, to, begin: clock, duration: d, lift: last ? lift : Math.min(lift, 26), heading, previous });
      clock += (k === 1 ? turnLead : .2) + d + (last ? .26 : .16);
      previous = heading;
    }
  }
  return { hops, end: clock + .55 };
}
function poseRoute(a, { hops }, t, face) {
  let hop = null;
  for (const h of hops) if (h.begin <= t) hop = h;
  const first = hops[0];
  const lead = hop && hop.previous === hop.heading && hop !== hops[0] ? .2 : turnLead;
  const delay = hop ? t - hop.begin - lead : -Infinity;
  const duration = hop?.duration ?? .5;
  const flight = THREE.MathUtils.clamp(delay / duration, 0, 1);
  const prep = pulse(delay, -.35, .35);
  const impact = pulse(delay, duration, .26);
  const airborne = Math.sin(Math.PI * flight);
  const travel = smooth(flight);
  const from = hop?.from ?? first.from, to = hop?.to ?? first.from;
  const x = THREE.MathUtils.lerp(from[0], to[0], travel), y = THREE.MathUtils.lerp(from[1], to[1], travel);
  const previous = hop?.previous ?? face;
  const delta = hop ? shortestTurn(previous, hop.heading) : 0;
  const turning = smooth((delay + lead) / (lead - .1));
  let yaw = previous + delta * turning;
  const last = hops.at(-1);
  const after = t - (last.begin + lead + last.duration + .3);
  if (after > 0) yaw = last.heading + shortestTurn(last.heading, face) * smooth(after / .6);
  place(a, x, y, airborne * (hop?.lift ?? 0), yaw, size, prep * .24 + impact * .20, airborne * .10, t);
  a.upper.rotation.x = airborne * (.20 - .44 * flight);
  a.upper.rotation.z = Math.sin(Math.PI * turning) * Math.sign(delta) * .13;
  setEyes(a.eyes, blink(t, .9, 3.7));
}

// Hiding: the frog lives behind a panel. Every cycle it peeks its head over
// the edge, and once per cycle it hops out, looks around and hops back. It is
// always on the ground: in front of a panel only when it is closer to the
// audience than the panel's foot, never on top of one.
const hidePeriod = 15, outHop = { begin: 7.4, duration: .62 }, backHop = { begin: 11.6, duration: .58 };
function peekAmount(u) {
  if (u < 2.2) return 0;
  if (u < 2.75) return smooth((u - 2.2) / .55);
  if (u < 5.1) return 1;
  if (u < 5.45) return 1 - smooth((u - 5.1) / .35);
  return 0;
}
// The body never moves while peeking: the frog stretches up (or leans out)
// so only its head comes past the edge, hat tip showing all the while.
function hidePose(spot) {
  if (spot.side) return { x: spot.x - 26, y: spot.y, lift: 0 };
  return { x: spot.x, y: spot.y + hatTop * cosP * size - 10, lift: 0 };
}
function posePeek(a, spot, t, playing) {
  const u = playing ? ((t % hidePeriod) + hidePeriod) % hidePeriod : 4;
  const hidden = hidePose(spot), out = { x: spot.out[0], y: spot.out[1], lift: 0 };
  const outLand = outHop.begin + turnLead + outHop.duration;
  if (u < outHop.begin || u >= backHop.begin + turnLead + backHop.duration) {
    const k = peekAmount(u);
    place(a, hidden.x, hidden.y, 0, (spot.side ? .18 : 0) + k * Math.sin(t * 1.3) * .22, size, 0, 0, t);
    a.shadow.visible = false; // the ground under it is behind the panel too
    if (spot.side) a.upper.position.set(k * 1.15, k * .12, 0);
    else a.upper.position.set(0, k * 1.35, 0);
    a.upper.rotation.x = a.upper.rotation.z = 0;
    setEyes(a.eyes, k > .5 ? blink(t, 1.4, 2.9) : 1);
    return;
  }
  const hop = u < backHop.begin ? outHop : backHop;
  const [from, to] = hop === outHop ? [hidden, out] : [out, hidden];
  const delay = u - hop.begin - turnLead;
  const flight = THREE.MathUtils.clamp(delay / hop.duration, 0, 1);
  const travel = smooth(flight), airborne = Math.sin(Math.PI * flight);
  const heading = Math.atan2(to.x - from.x, (to.y - from.y) / cosP);
  const turning = smooth((delay + turnLead) / (turnLead - .1));
  let yaw = shortestTurn(0, heading) * turning;
  const looking = u - outLand - .3;
  if (hop === outHop && looking > 0) yaw = heading + shortestTurn(heading, Math.sin(looking * 1.7) * .62) * smooth(looking / .5);
  const x = THREE.MathUtils.lerp(from.x, to.x, travel), y = THREE.MathUtils.lerp(from.y, to.y, travel);
  const lift = THREE.MathUtils.lerp(from.lift, to.lift, travel) + airborne * 44;
  place(a, x, y, lift, yaw, size, pulse(delay, -.35, .35) * .24 + pulse(delay, hop.duration, .26) * .2, airborne * .1, t);
  a.upper.rotation.x = airborne * (.20 - .44 * flight);
  a.upper.rotation.z = 0;
  setEyes(a.eyes, blink(t, .9, 3.7));
}

const scenes = {
  signal: { face: .25, route: ({ unknown: r }) => [[r.r - 34, r.b + 16], [r.l + 131, r.b + 16, 32, .6]] },
  loop: { face: 0, route: ({ watch, system, context, act }) => [watch, system, context, act].map((r, i) => [r.cx, r.b + 92, i === 3 ? 30 : 26, i === 3 ? .78 : .5]) },
  receipt: { face: -.2, route: ({ dm, paper }) => [[dm.l + 60, dm.b + 78], [dm.l + 64, paper.t + 24, 26, .56], [paper.l - 48, paper.b - 10, 20, .5]] },
  forecast: { face: .1, route: ({ chat, forecast }) => [[chat.r - 70, chat.b + 54], [forecast.l + 74, forecast.b + 54, 30, .56]] },
  record: { face: .15, route: ({ filed: r }) => [[r.r - 261, r.b - 10], [r.r - 117, r.b - 10, 30, .58]] },
  // Behind a card: peek over its top edge, hop back into the open ground above it.
  'hide-top': { peek: ({ cover: r }) => ({ x: r.r - 92, y: r.t, cover: r, out: [r.r - 92, r.t - 10] }) },
  // Behind a node: peek over the top, hop out beside it.
  'hide-node': { peek: ({ cover: r }) => ({ x: r.r - 68, y: r.t, cover: r, out: [r.r + 64, r.b - 14] }) },
  // Around a panel's side: peek past the right edge, hop a little further out
  // beside it, still behind the panel, never in front of it.
  'hide-side': { peek: ({ cover: r }) => ({ x: r.r, y: r.b - 42, cover: r, side: true, out: [r.r + 36, r.b - 46] }) },
  close: {
    face: .1,
    route: ({ meadow: r }) => [[r.l - 70, r.b - 62], [r.l + 149, r.b - 20, 40, .6]],
    company: ({ meadow: r }) => [[r.l + 40, r.b + 36, .68, .4], [r.l + 233, r.b - 45, .74, -.5], [r.l + 306, r.b - 4, .83, -.3]],
  },
};

function measure(slide) {
  const box = slide.getBoundingClientRect();
  const scale = box.width / slide.offsetWidth || 1;
  return Object.fromEntries([...slide.querySelectorAll('[data-perch]')].map(el => {
    const b = el.getBoundingClientRect();
    const l = (b.left-box.left)/scale, r = (b.right-box.left)/scale;
    return [el.dataset.perch, { l, r, t: (b.top-box.top)/scale, b: (b.bottom-box.top)/scale, cx: (l+r)/2 }];
  }));
}

// A depth-only plane in front of the frog lets the slide's own panel show
// through the transparent canvas, so the frog is really behind it.
let occluders = [];
function occlude(r) {
  ray.setFromCamera(ndc((r.l + r.r) / 2, (r.t + r.b) / 2), camera);
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(r.r - r.l, r.b - r.t), new THREE.MeshBasicMaterial({ colorWrite: false }));
  plane.position.copy(ray.ray.at(600, new THREE.Vector3()));
  plane.quaternion.copy(camera.quaternion);
  plane.renderOrder = -1; // write depth before the frog is drawn
  scene.add(plane);
  occluders.push({ plane, rect: r });
}

const state = { slide: null, scene: null, start: 0, playing: false, paused: false, seekTime: 0, end: 0, data: null };
let raf = 0;
function render(t) {
  const s = state.data;
  if (!s) return;
  if (s.hops) poseRoute(guide, s, t, s.face);
  else posePeek(guide, s.spot, t, state.playing);
  s.company?.forEach(([x, y, k, yaw], i) => {
    const c = companions[i];
    place(c, x, y, 0, yaw, size * k, 0, 0, t + i);
    setEyes(c.eyes, blink(t, i * 1.1, 4.1 + i * .5));
  });
  // A panel stands on its bottom edge: the frog is drawn behind it unless its feet are nearer the audience.
  const foot = toStage(guide.root.position.clone().setY(0).project(camera));
  occluders.forEach(o => { o.plane.visible = foot.y <= o.rect.b; });
  key.target.position.copy(guide.root.position);
  key.position.copy(guide.root.position).add(new THREE.Vector3(-240, 460, 300));
  renderer.render(scene, camera);
}
function loop(now) {
  raf = requestAnimationFrame(loop);
  if (state.paused || !state.playing) return;
  render((now - state.start) / 1000);
}
function reset() {
  cancelAnimationFrame(raf);
  occluders.forEach(o => { scene.remove(o.plane); o.plane.geometry.dispose(); });
  occluders = [];
  [guide, ...companions].forEach(a => { a.root.visible = a.shadow.visible = false; });
  state.data = null;
  state.playing = state.paused = false;
  state.scene = null;
  canvas.hidden = true;
  state.slide?.classList.remove('reveal');
}
export function visit(slide, play) {
  reset();
  state.slide = slide;
  const name = slide?.dataset.mascotScene;
  const spec = scenes[name];
  if (!spec) return;
  const perches = measure(slide);
  const data = { face: spec.face ?? 0 };
  if (spec.route) Object.assign(data, buildHops(spec.route(perches), data.face));
  else {
    data.spot = spec.peek(perches);
    data.end = hidePeriod;
    occlude(data.spot.cover);
  }
  if (spec.company) data.company = spec.company(perches);
  state.data = data;
  state.scene = name;
  state.end = data.end;
  canvas.hidden = false;
  if (name === 'close') {
    const reveal = slide.querySelector('.guide-reveal');
    if (reveal) reveal.textContent = `Did you keep count? The red hat was on ${document.querySelectorAll('[data-mascot-scene]').length - 1} slides. It hid on ${document.querySelectorAll('[data-mascot-scene^="hide"]').length} of them.`;
  }
  if (play && !still()) {
    state.playing = true;
    state.start = performance.now();
    raf = requestAnimationFrame(loop);
  } else {
    render(data.hops ? data.end + 9 : 0);
    if (name === 'close' && document.documentElement.dataset.embed) slide.classList.add('reveal');
  }
}
reduced.addEventListener('change', () => visit(state.slide, false));

// Test hooks: freeze at a moment and read the frog's visible stage rect.
function project(a) {
  const box = new THREE.Box3().setFromObject(a.root);
  const pts = [];
  for (let i = 0; i < 8; i++) {
    const v = new THREE.Vector3(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z);
    pts.push(toStage(v.project(camera)));
  }
  const rect = { l: Math.min(...pts.map(p => p.x)), r: Math.max(...pts.map(p => p.x)), t: Math.min(...pts.map(p => p.y)), b: Math.max(...pts.map(p => p.y)) };
  for (const { rect: o, plane } of occluders) {
    if (!plane.visible) continue;
    if (rect.l >= o.l && rect.r <= o.r && rect.t >= o.t && rect.b <= o.b) return null;
    if (o.l <= rect.l && o.r >= rect.r && o.t > rect.t && o.t < rect.b) rect.b = o.t;
    else if (o.t <= rect.t && o.b >= rect.b && o.r > rect.l && o.r < rect.r) rect.l = o.r;
  }
  return rect;
}
export const guideApi = {
  get scene() { return state.scene; },
  get playing() { return state.playing; },
  get start() { return state.start; },
  get end() { return state.end; },
  seek(t) { state.paused = true; render(t); },
  resume() { state.paused = false; },
  bounds() { return project(guide); },
  company() { return companions.filter(c => c.root.visible).map(project); },
  visit,
  // The presenter reveals the count with one more Next on the last slide.
  reveal() { if (state.scene === 'close') state.slide.classList.add('reveal'); },
};
