// Frogs for pitch.html. The herd on the cover and the close is the one from
// frogs.js; the red-hat frog that walks the progress track is built like the
// guide in scout-guide.js. Bundle for file:// use:
//   esbuild presentaito/pitch-frogs.js --bundle --format=iife --minify --outfile=presentaito/pitch-frogs.bundle.js
import * as THREE from './vendor/three.module.js';

const stage = document.getElementById('stage');
const still = new URLSearchParams(location.search).has('still') || matchMedia('(prefers-reduced-motion: reduce)').matches;
const { clamp, lerp } = THREE.MathUtils;
const smooth = x => { x = clamp(x, 0, 1); return x*x*(3-2*x); };
const shortestTurn = (from, to) => Math.atan2(Math.sin(to - from), Math.cos(to - from));
const pulse = (t, start, width) => smooth((t - start) / (width*.45)) * (1 - smooth((t - start - width*.45) / (width*.55)));

// ---------- one clay tree frog (frogs.js / frog-model.js) ----------
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
    piece.castShadow = true;
    hat.add(piece);
  };
  fit(new THREE.CylinderGeometry(.45, .47, .035, 40), red, 0, .8);
  const crown = [[0, 0], [.27, 0], [.27, .07], [.24, .24], [.19, .29], [.08, .26], [0, .265]];
  fit(new THREE.LatheGeometry(crown.map(([r, y]) => new THREE.Vector2(r, y)), 40), red, .012, .82);
  fit(new THREE.CylinderGeometry(.272, .277, .075, 40, 1, true), ribbon, .061, .82);
}
// The frog stands on y = 0 and faces local +Z.
function buildFrog({ hue, hat = false }) {
  const root = new THREE.Group();
  const model = new THREE.Group();
  const upper = new THREE.Group();
  root.add(model);
  model.add(upper);
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
    if (mesh.geometry === ball && mesh.scale.x > .17 && [skin, pads, belly].includes(mesh.material)) mesh.castShadow = true;
  });
  if (hat) addFedora(upper);
  const shadow = new THREE.Mesh(shadowPlane, new THREE.MeshBasicMaterial({ map: shadowMap, transparent: true, depthWrite: false }));
  shadow.rotation.set(-Math.PI / 2, 0, 0, 'YXZ');
  const frog = { root, model, upper, skin, belly, pads, eyes, shadow, hue: new THREE.Color(hue) };
  tint(frog, 1);
  return frog;
}
function tint(f, k) {
  f.skin.color.copy(clay).lerp(f.hue, k);
  f.belly.color.copy(cream).lerp(f.hue, k * .27);
  f.pads.color.copy(clay).lerp(f.hue, k * .82);
}
function setEyes(eyes, openness) {
  for (const eye of eyes) {
    eye.open.visible = openness > .12;
    eye.open.scale.y = openness;
    eye.closed.visible = openness <= .24;
  }
}
function makeRenderer(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(1600, 900, false);
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  return renderer;
}

// ---------- the herd: sixteen frogs with seeded roaming and reserved hop paths ----------
function createHerd(canvas) {
  const renderer = makeRenderer(canvas);
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
  ].map(spec => {
    const frog = { ...spec, ...buildFrog(spec) };
    scene.add(frog.root, frog.shadow);
    return frog;
  });

  const turnLead = .70;
  // Reserve a swept disk from the start of the turn until landing. A resting
  // frog reserves just its current spot, including during the wake-up beat.
  function pointPathDistance(p, a, b) {
    const dx = b.x - a.x, dz = b.z - a.z;
    const len = dx*dx + dz*dz;
    const u = len ? clamp(((p.x-a.x)*dx + (p.z-a.z)*dz) / len, 0, 1) : 0;
    return Math.hypot(p.x-a.x-u*dx, p.z-a.z-u*dz);
  }
  const sideOf = (a, b, p) => (b.x-a.x)*(p.z-a.z) - (b.z-a.z)*(p.x-a.x);
  function pathDistance(a, b, c, d) {
    if (sideOf(a,b,c)*sideOf(a,b,d) < 0 && sideOf(c,d,a)*sideOf(c,d,b) < 0) return 0;
    return Math.min(pointPathDistance(a,c,d), pointPathDistance(b,c,d), pointPathDistance(c,a,b), pointPathDistance(d,a,b));
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
        if (Math.abs(to.x) > xLimit || to.z < -3.7 || to.z > 2.18 || Math.hypot(to.x-r.home.x, to.z-r.home.z) > 2.1) continue;
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
      tint(f, smooth((a - .13) / 1.4));
      const period = 3.3 + i * .59;
      const recurring = a > 2.2 ? pulse((a - 2.2) % period, 1.1 + i * .22, .19) : 0;
      setEyes(f.eyes, smooth(a / .20) * (1 - .96 * Math.max(pulse(a,.28,.23), recurring)));

      const hop = hopAt(roamers[i].trail, t);
      const delay = hop ? t - hop.begin - turnLead : -Infinity;
      const duration = hop?.duration ?? .58;
      const flight = clamp(delay / duration, 0, 1);
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
      f.root.position.set(lerp(from.x, to.x, travel), y, lerp(from.z, to.z, travel));
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
  return { renderer, pose, count: frogs.length };
}

// ---------- the guide: the red-hat frog hops along the progress track ----------
function createGuide(canvas) {
  const renderer = makeRenderer(canvas);
  // Orthographic view in stage pixels, pitched like the herd so the ground
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
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.bias = -.0006;
  key.shadow.radius = 3.25;
  Object.assign(key.shadow.camera, { left: -240, right: 240, top: 240, bottom: -240, near: 1, far: 2000 });
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
  function groundAt(x, y) {
    ray.setFromCamera(new THREE.Vector2(x / 800 - 1, 1 - y / 450), camera);
    return ray.ray.intersectPlane(ground, new THREE.Vector3());
  }

  const frog = buildFrog({ hue: '#71964b', hat: true });
  frog.root.visible = frog.shadow.visible = false;
  scene.add(frog.root, frog.shadow);
  const size = 33, face = .12, turnLead = .3, chainLead = .14, maxHop = 150;
  const blink = (t, phase, period) => 1 - .96 * pulse(((t + phase) % period + period) % period, period * .55, .19);

  // Place the frog: foot on the ground at stage point (x, y), lifted `lift`
  // screen pixels, facing `yaw` (0 looks at the audience).
  function place(s, t) {
    const p = groundAt(s.x, s.y);
    const up = s.lift / cosP;
    frog.root.position.set(p.x, up, p.z);
    frog.root.rotation.y = s.yaw;
    frog.root.scale.setScalar(size);
    frog.upper.scale.set(1 + s.squash*.36, 1 - s.squash + s.stretch + Math.sin(t*2.1) * .019, 1 + s.squash*.2);
    frog.upper.rotation.x = s.tiltX;
    frog.upper.rotation.z = s.tiltZ;
    const airborne = Math.min(1, up / 40);
    frog.shadow.position.set(p.x, .5, p.z);
    frog.shadow.rotation.y = s.yaw;
    frog.shadow.scale.set(size * (3.2 + airborne * .6), size * (2.65 + airborne * .6), 1);
    frog.shadow.material.opacity = .74 - .55 * airborne;
    frog.root.visible = frog.shadow.visible = true;
    key.target.position.copy(frog.root.position);
    key.position.copy(frog.root.position).add(new THREE.Vector3(-240, 460, 300));
  }

  // No single hop is much longer than two frogs: a long leg becomes a few hops.
  function buildRoute(from, to, startYaw) {
    const hops = [];
    const dx = to.x - from.x, dy = to.y - from.y;
    const parts = clamp(Math.ceil(Math.hypot(dx, dy / cosP) / maxHop), 1, 4);
    const heading = Math.atan2(dx, dy / cosP);
    let clock = .03, previous = startYaw;
    for (let k = 1; k <= parts; k++) {
      const last = k === parts;
      const duration = last ? .5 : .4;
      hops.push({
        from: { x: lerp(from.x, to.x, (k-1)/parts), y: lerp(from.y, to.y, (k-1)/parts) },
        to: { x: lerp(from.x, to.x, k/parts), y: lerp(from.y, to.y, k/parts) },
        begin: clock, duration, lift: last ? 30 : 22, heading, previous, lead: k === 1 ? turnLead : chainLead,
      });
      clock += (k === 1 ? turnLead : chainLead) + duration + (last ? .2 : .06);
      previous = heading;
    }
    return { hops, startYaw, end: clock + .75 };
  }
  function sample(route, t) {
    const { hops } = route;
    let hop = null;
    for (const h of hops) if (h.begin <= t) hop = h;
    const first = hops[0], last = hops.at(-1);
    const lead = hop?.lead ?? turnLead;
    const delay = hop ? t - hop.begin - lead : -Infinity;
    const duration = hop?.duration ?? .5;
    const flight = clamp(delay / duration, 0, 1);
    const airborne = Math.sin(Math.PI * flight), travel = smooth(flight);
    const from = hop?.from ?? first.from, to = hop?.to ?? first.from;
    const previous = hop?.previous ?? route.startYaw;
    const delta = hop ? shortestTurn(previous, hop.heading) : 0;
    const turning = smooth((delay + lead) / Math.max(.05, lead - .08));
    let yaw = previous + delta * turning;
    const after = t - (last.begin + last.lead + last.duration + .2);
    // Back to the audience after landing, then a slow look around.
    if (after > 0) yaw = last.heading + shortestTurn(last.heading, face) * smooth(after / .5) + Math.sin(after * .55) * .2 * smooth((after - 1.2) / 2);
    return {
      x: lerp(from.x, to.x, travel), y: lerp(from.y, to.y, travel), lift: airborne * (hop?.lift ?? 0), yaw,
      squash: pulse(delay, -.3, .3) * .24 + pulse(delay, duration, .26) * .2, stretch: airborne * .1,
      tiltX: airborne * (.20 - .44 * flight), tiltZ: Math.sin(Math.PI * turning) * Math.sign(delta) * .13,
    };
  }

  let route = null, t0 = 0, shown = false;
  function goTo(x, y, now, dir) {
    let from, yaw = face;
    if (shown && route) {
      const s = sample(route, (now - t0) / 1000);
      from = { x: s.x, y: s.y };
      yaw = s.yaw;
    } else from = { x: x - dir * 190, y };
    if (still) from = { x, y };
    if (shown && Math.hypot(from.x - x, from.y - y) < 2) return;
    route = buildRoute(from, { x, y }, yaw);
    t0 = now;
    shown = true;
    if (still) render(now + 60000);
  }
  function hide() {
    shown = false;
    route = null;
    setTimeout(() => {
      if (shown) return;
      frog.root.visible = frog.shadow.visible = false;
      renderer.render(scene, camera);
    }, 500);
  }
  function render(now) {
    if (!shown || !route) return;
    const t = (now - t0) / 1000;
    place(sample(route, t), t);
    setEyes(frog.eyes, blink(t, .9, 3.7));
    renderer.render(scene, camera);
  }
  return { renderer, goTo, hide, render, get shown() { return shown; } };
}

// ---------- wiring to the deck ----------
try {
  const herd = createHerd(document.getElementById('herd'));
  const guide = createGuide(document.getElementById('guide'));
  let mode = null, herdStart = 0;
  function onSlide(d) {
    const now = performance.now();
    if (d.frogs === 'herd') {
      mode = 'herd';
      herdStart = now;
      guide.hide();
      if (still) herd.pose(7.4);
    } else if (d.frogs === 'guide') {
      mode = 'guide';
      guide.goTo(d.tick.x, d.tick.y, now, d.prev > d.index ? -1 : 1);
    } else {
      mode = null;
      guide.hide();
    }
  }
  function onFit(scale) {
    const ratio = clamp(devicePixelRatio * scale, 1, 2);
    for (const { renderer } of [herd, guide]) {
      renderer.setPixelRatio(ratio);
      renderer.setSize(1600, 900, false);
    }
    if (still && window.deck) onSlide(window.deck.state());
  }
  addEventListener('deck:slide', e => onSlide(e.detail));
  addEventListener('deck:fit', e => onFit(e.detail.scale));
  if (window.deck) {
    onFit(window.deck.scale());
    onSlide(window.deck.state());
  }
  stage.classList.add('frogs-ready');
  if (!still) {
    const frame = now => {
      requestAnimationFrame(frame);
      if (mode === 'herd') herd.pose((now - herdStart) / 1000);
      else if (mode === 'guide') guide.render(now);
    };
    requestAnimationFrame(frame);
  }
  // Test hooks: freeze the herd at a moment.
  window.__frogs = { count: herd.count, sim(sec) { mode = null; herd.pose(sec); } };
} catch (error) {
  console.warn('Frogs are off:', error);
  stage.classList.add('no-frogs');
}
