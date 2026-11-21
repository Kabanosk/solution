// Scout — sixteen clay tree frogs with seeded roaming and reserved hop paths.
import * as THREE from 'three';

const canvas = document.getElementById('crabs');
const slide = canvas.closest('.slide');
const still = !!document.documentElement.dataset.embed || matchMedia('(prefers-reduced-motion: reduce)').matches;
const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.setSize(1600, 900, false);
renderer.toneMapping = THREE.NeutralToneMapping;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(28, 16 / 9, 0.1, 60);
camera.position.set(0, 6.5, 18);
camera.lookAt(0, 2.35, 0);
scene.add(new THREE.HemisphereLight(0xfbfff2, 0x869170, 1.75));
const key = new THREE.DirectionalLight(0xfffcee, 2.0);
key.position.set(-5, 9, 6);
key.castShadow = true;
key.shadow.mapSize.set(1024, 1024);
key.shadow.bias = -.0004;
key.shadow.radius = 3.25;
Object.assign(key.shadow.camera, { left: -9.5, right: 9.5, top: 6, bottom: -6, near: 1, far: 26 });
scene.add(key);
const floor = new THREE.Mesh(new THREE.PlaneGeometry(34, 20), new THREE.ShadowMaterial({ color: '#202820', opacity: .13, depthWrite: false }));
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);
const rim = new THREE.DirectionalLight(0xe8ffc5, 1.1);
rim.position.set(4, 5, -5);
scene.add(rim);

const shadowImage = document.createElement('canvas');
shadowImage.width = shadowImage.height = 128;
const ctx = shadowImage.getContext('2d');
const radial = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
radial.addColorStop(0, 'rgba(36,51,34,.46)');
radial.addColorStop(.42, 'rgba(36,51,34,.22)');
radial.addColorStop(.76, 'rgba(36,51,34,.065)');
radial.addColorStop(1, 'rgba(36,51,34,0)');
ctx.fillStyle = radial;
ctx.fillRect(0, 0, 128, 128);
const shadowMap = new THREE.CanvasTexture(shadowImage);
shadowMap.colorSpace = THREE.SRGBColorSpace;

const ball = new THREE.SphereGeometry(1, 32, 24);
const shadowPlane = new THREE.PlaneGeometry(1, 1);
const clay = new THREE.Color('#e5e6d9');
const cream = new THREE.Color('#f1ecda');
const pupil = new THREE.MeshPhysicalMaterial({ color: '#17251b', roughness: .25, clearcoat: .55 });
const glint = new THREE.MeshBasicMaterial({ color: '#ffffff' });
const ink = new THREE.MeshStandardMaterial({ color: '#203026', roughness: .58 });
const coat = color => new THREE.MeshPhysicalMaterial({ color, roughness: .69, clearcoat: .12, clearcoatRoughness: .48 });
function ellipsoid(parent, mat, x, y, z, sx, sy, sz) {
  const mesh = new THREE.Mesh(ball, mat);
  mesh.position.set(x, y, z);
  mesh.scale.set(sx, sy, sz);
  parent.add(mesh);
  return mesh;
}
function line(parent, points, radius) {
  const curve = new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p)));
  parent.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 16, radius, 7, false), ink));
}

function addFedora(head) {
  const hat = new THREE.Group();
  hat.position.set(0, 1.24, .07);
  hat.rotation.set(.13, -.10, -.06);
  hat.scale.setScalar(1.5);
  head.add(hat);
  const red = coat('#d01829');
  const ribbon = new THREE.MeshStandardMaterial({ color: '#0b0b0b', roughness: .9 });
  const fit = (geometry, material, y, depth) => {
    const piece = new THREE.Mesh(geometry, material);
    piece.position.y = y;
    piece.scale.z = depth;
    hat.add(piece);
  };
  fit(new THREE.CylinderGeometry(.45, .47, .035, 40), red, 0, .8);
  const crown = [[0, 0], [.27, 0], [.27, .07], [.24, .24], [.19, .29], [.08, .26], [0, .265]];
  fit(new THREE.LatheGeometry(crown.map(([r, y]) => new THREE.Vector2(r, y)), 40), red, .012, .82);
  fit(new THREE.CylinderGeometry(.272, .277, .075, 40, 1, true), ribbon, .061, .82);
}

function makeFrog(spec) {
  const root = new THREE.Group();
  const model = new THREE.Group();
  const upper = new THREE.Group();
  root.add(model);
  model.add(upper);
  scene.add(root);
  const skin = coat(clay.clone());
  const belly = coat(cream.clone());
  const pads = coat(clay.clone());
  ellipsoid(upper, skin, 0, .55, -.12, .77, .53, .77);
  ellipsoid(upper, skin, 0, .71, .42, .79, .45, .58);
  ellipsoid(upper, belly, 0, .435, .56, .59, .27, .37);
  line(upper, [[-.43,.60,.88],[-.24,.53,.962],[0,.51,.989],[.24,.53,.962],[.43,.60,.88]], .016);
  const eyes = [];
  for (const side of [-1, 1]) {
    ellipsoid(upper, skin, .51 * side, 1.02, .37, .35, .37, .34);
    const open = new THREE.Group();
    open.position.set(.54 * side, 1.047, .606);
    open.rotation.y = side * .16;
    upper.add(open);
    ellipsoid(open, belly, 0, 0, 0, .226, .23, .128);
    ellipsoid(open, pupil, -.024 * side, 0, .11, .137, .155, .06);
    ellipsoid(open, glint, -.064, .063, .16, .036, .036, .012);
    const closed = new THREE.Group();
    closed.position.copy(open.position);
    closed.rotation.copy(open.rotation);
    upper.add(closed);
    line(closed, [[-.17,.018,.047],[-.09,-.031,.09],[0,-.046,.113],[.09,-.031,.09],[.17,.018,.047]], .023);
    eyes.push({ open, closed });
    ellipsoid(upper, skin, .69 * side, .33, -.31, .38, .31, .54).rotation.y = -.34 * side;
    ellipsoid(upper, skin, .55 * side, .32, .55, .172, .28, .20).rotation.z = .24 * side;
    ellipsoid(model, pads, .70 * side, .103, -.01, .31, .10, .35);
    ellipsoid(model, skin, .56 * side, .084, .74, .176, .084, .21);
    for (let toe = -1; toe <= 1; toe++) {
      ellipsoid(model, pads, .56 * side + toe * .101, .065, .96 - Math.abs(toe) * .045, .068, .054, .10).rotation.y = toe * .23;
    }
  }
  // Cast from the body, eyes and feet, not the tiny toes and facial details.
  model.traverse(mesh => {
    if (mesh.geometry === ball && mesh.scale.x > .17 && [skin, pads, belly].includes(mesh.material)) {
      mesh.castShadow = true;
    }
  });
  if (spec.hat) addFedora(upper);
  const shadow = new THREE.Mesh(shadowPlane, new THREE.MeshBasicMaterial({ map: shadowMap, transparent: true, depthWrite: false }));
  shadow.rotation.set(-Math.PI / 2, 0, 0, 'YXZ');
  scene.add(shadow);
  return { ...spec, root, model, upper, skin, belly, pads, eyes, shadow, hue: new THREE.Color(spec.hue) };
}

const frogs = [
  { x: -5.55, z: -1.79, size: 0.66, width: 0.94, yaw: 0.23, wake: 0.65, hop: 3.02, lift: 0.62, hue: '#71964b', hat: true },
  { x: -4.57, z: 0.14, size: 0.83, width: 0.99, yaw: -0.14, wake: 1.27, hop: 4.19, lift: 0.76, hue: '#45834d' },
  { x: -2.86, z: -1.61, size: 0.58, width: 0.93, yaw: -0.24, wake: 1.7, hop: 5.15, lift: 0.6, hue: '#91af55' },
  { x: -1.42, z: 0.3, size: 0.78, width: 1.05, yaw: 0.13, wake: 0.95, hop: 3.59, lift: 0.81, hue: '#62a063' },
  { x: 0.55, z: -1.68, size: 0.63, width: 0.93, yaw: -0.38, wake: 1.47, hop: 4.66, lift: 0.68, hue: '#648f45' },
  { x: 2.21, z: 0.27, size: 0.72, width: 0.91, yaw: -0.25, wake: 1.92, hop: 5.72, lift: 0.7, hue: '#a2be60' },
  { x: 3.9, z: -1.55, size: 0.67, width: 1.03, yaw: -0.45, wake: 2.12, hop: 6.27, lift: 0.72, hue: '#4f9462' },
  { x: 5.39, z: 0.16, size: 0.72, width: 0.96, yaw: -0.25, wake: 1.04, hop: 3.86, lift: 0.66, hue: '#86a54d' },
  { x: -3.28, z: 1.83, size: 0.63, width: 1.02, yaw: 0.19, wake: 1.76, hop: 4.96, lift: 0.66, hue: '#679b57' },
  { x: 0.47, z: 1.84, size: 0.84, width: 0.97, yaw: -0.14, wake: 0.78, hop: 3.31, lift: 0.79, hue: '#4b8755' },
  { x: 3.76, z: 1.86, size: 0.66, width: 1.05, yaw: -0.24, wake: 1.6, hop: 4.46, lift: 0.66, hue: '#77a154' },
  { x: -6.45, z: .72, size: .64, width: .96, yaw: .24, wake: 1.42, hop: 4.35, lift: .67, hue: '#89a84e' },
  { x: -4.3, z: -3.15, size: .7, width: 1.01, yaw: .32, wake: .88, hop: 3.42, lift: .71, hue: '#5d9255' },
  { x: -.98, z: -3.28, size: .58, width: .92, yaw: -.16, wake: 1.59, hop: 4.58, lift: .63, hue: '#9ab657' },
  { x: 2.43, z: -3.15, size: .69, width: .98, yaw: -.37, wake: 1.15, hop: 3.98, lift: .69, hue: '#63994c' },
  { x: 6.32, z: -1.4, size: .62, width: 1.04, yaw: -.31, wake: 1.82, hop: 5.38, lift: .65, hue: '#80a858' },
].map(makeFrog);

const smooth = x => { x = THREE.MathUtils.clamp(x, 0, 1); return x*x*(3-2*x); };
// Local +Z is the frog's front. Match it to the hop's world-space direction.
const shortestTurn = (from, to) => Math.atan2(Math.sin(to - from), Math.cos(to - from));
function pulse(t, start, width) {
  return smooth((t - start) / (width*.45)) * (1 - smooth((t - start - width*.45) / (width*.55)));
}
const turnLead = .70;
// Reserve a swept disk from the start of the turn until landing. A resting
// frog reserves just its current spot, including during the wake-up beat.
function pointPathDistance(p, a, b) {
  const dx = b.x - a.x, dz = b.z - a.z;
  const len = dx*dx + dz*dz;
  const u = len ? THREE.MathUtils.clamp(((p.x-a.x)*dx + (p.z-a.z)*dz) / len, 0, 1) : 0;
  return Math.hypot(p.x-a.x-u*dx, p.z-a.z-u*dz);
}
const sideOf = (a, b, p) => (b.x-a.x)*(p.z-a.z) - (b.z-a.z)*(p.x-a.x);
function pathDistance(a, b, c, d) {
  if (sideOf(a,b,c)*sideOf(a,b,d) < 0 && sideOf(c,d,a)*sideOf(c,d,b) < 0) return 0;
  return Math.min(pointPathDistance(a,c,d), pointPathDistance(b,c,d),
    pointPathDistance(c,a,b), pointPathDistance(d,a,b));
}

let seed = 0xd32a7f91;
function random() {
  seed ^= seed << 13;
  seed ^= seed >>> 17;
  seed ^= seed << 5;
  return (seed >>> 0) / 4294967296;
}
const roamers = frogs.map(f => ({
  home: f, at: { x: f.x, z: f.z }, heading: f.yaw,
  // Allow for the widest legs, toes and the tilted body during turns.
  radius: 1.38 * f.size * .4125 * Math.max(1, f.width),
  next: f.hop - turnLead, trail: [],
}));
function extendRoutes(t) {
  while (true) {
    const r = roamers.reduce((first, other) => other.next < first.next ? other : first);
    const begin = r.next;
    if (begin > t) return;
    const from = r.at;
    for (let attempt = 0; attempt < 24; attempt++) {
      const heading = (random() - .5) * 2 * Math.PI;
      // Prefer a change of direction without constantly turning right back.
      const turn = Math.abs(shortestTurn(r.heading, heading));
      if (attempt < 10 && (turn < .18 || turn > 2.6)) continue;
      const length = .48 + random() * .85;
      const to = { x: from.x + Math.sin(heading)*length, z: from.z + Math.cos(heading)*length };
      const xLimit = 6.8 - Math.max(0, to.z) * .33;
      if (Math.abs(to.x) > xLimit || to.z < -3.7 || to.z > 2.18 ||
          Math.hypot(to.x-r.home.x, to.z-r.home.z) > 2.1) continue;
      if (roamers.some(other => {
        if (r === other) return false;
        const hop = other.trail.at(-1);
        const start = hop && hop.end > begin ? hop.from : other.at;
        return pathDistance(from, to, start, other.at) < r.radius + other.radius + .06;
      })) continue;
      const duration = .49 + length * .14;
      const end = begin + turnLead + duration;
      r.trail.push({ from, to, begin, end, duration, heading, previous: r.heading });
      r.at = to;
      r.heading = heading;
      r.next = end + .78 + random()*2.1 + (random() < .24 ? random()*1.9 : 0);
      break;
    }
    if (r.next === begin) r.next += .38 + random()*.7;
  }
}
function hopAt(trail, t) {
  let lo = 0, hi = trail.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (trail[mid].begin <= t) lo = mid + 1;
    else hi = mid;
  }
  return trail[lo - 1];
}
function pose(t) {
  extendRoutes(t);
  frogs.forEach((f, i) => {
    const a = t - f.wake;
    const color = smooth((a - .13) / 1.4);
    f.skin.color.copy(clay).lerp(f.hue, color);
    f.belly.color.copy(cream).lerp(f.hue, color * .27);
    f.pads.color.copy(clay).lerp(f.hue, color * .82);
    const period = 3.3 + i * .59;
    const recurring = a > 2.2 ? pulse((a - 2.2) % period, 1.1 + i * .22, .19) : 0;
    const openness = smooth(a / .20) * (1 - .96 * Math.max(pulse(a,.28,.23), recurring));
    for (const eye of f.eyes) {
      eye.open.visible = openness > .12;
      eye.open.scale.y = openness;
      eye.closed.visible = openness <= .24;
    }

    const hop = hopAt(roamers[i].trail, t);
    const delay = hop ? t - hop.begin - turnLead : -Infinity;
    const duration = hop?.duration ?? .58;
    const flight = THREE.MathUtils.clamp(delay / duration, 0, 1);
    const prep = pulse(delay, -.35, .35);
    const impact = pulse(delay, duration, .26);
    const airborne = Math.sin(Math.PI * flight);
    const y = airborne * f.lift * .72;
    const travel = smooth(flight);
    const squash = prep * .24 + impact * .20;
    const stretch = airborne * .10;
    const breathing = Math.sin(t*2.1 + i) * .019;
    // Face the destination before takeoff; hold that heading in flight.
    const previousHeading = hop?.previous ?? f.yaw;
    const delta = hop ? shortestTurn(previousHeading, hop.heading) : 0;
    const turning = smooth((delay + turnLead) / (turnLead - .12));
    const from = hop?.from ?? f, to = hop?.to ?? f;
    f.root.position.set(THREE.MathUtils.lerp(from.x, to.x, travel), y, THREE.MathUtils.lerp(from.z, to.z, travel));
    f.root.rotation.y = previousHeading + delta * turning;
    const size = f.size * .4125;
    f.model.scale.set(size*f.width, size, size);
    f.upper.scale.set(1+squash*.36, 1-squash+stretch+breathing, 1+squash*.2);
    f.upper.rotation.x = airborne * (.20 - .44 * flight);
    f.upper.rotation.z = Math.sin(Math.PI * turning) * Math.sign(delta) * .13;
    f.upper.position.y = -.035 * (1 - smooth(a/.25));
    // Contact shadow anchors the body at rest and recedes as the cast shadow separates.
    f.shadow.position.set(f.root.position.x, .008, f.root.position.z);
    f.shadow.rotation.y = f.root.rotation.y;
    f.shadow.scale.set(size * (3.2 * f.width + y * 1.3), size * (2.65 + y * 1.3), 1);
    f.shadow.material.opacity = .74 - .55 * airborne;
  });
  renderer.render(scene, camera);
}

let start = performance.now(), wasOn = false, paused = false;
const replay = () => { paused = false; start = performance.now(); if (still) pose(7.4); };
pose(still ? 7.4 : 0);
slide.classList.add('frogs-ready');
document.querySelector('#replay-frogs').addEventListener('click', replay);
if (!still) {
  const on = now => {
    requestAnimationFrame(on);
    const active = slide.classList.contains('on');
    if (active && !wasOn) {
      start = now;
      paused = false;
      slide.classList.remove('play');
      void slide.offsetWidth;
      slide.classList.add('play');
    }
    wasOn = active;
    if (active && !paused) pose((now - start)/1000);
  };
  requestAnimationFrame(on);
}
window.__crabs = {
  count: frogs.length,
  sim(sec) { paused = true; pose(sec); },
  play: replay,
};
