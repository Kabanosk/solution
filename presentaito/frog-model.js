// One clay tree frog, built the same way as the sixteen on the title slide.
import * as THREE from 'three';

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
export const shadowMap = new THREE.CanvasTexture(shadowImage);
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

// The frog stands on y = 0 and faces local +Z. Its body is about 2.1 units
// wide and the hat top sits 1.68 units up.
export function makeFrogModel({ hue, hat = false }) {
  const root = new THREE.Group();
  const model = new THREE.Group();
  const upper = new THREE.Group();
  root.add(model);
  model.add(upper);
  const tint = new THREE.Color(hue);
  const skin = coat(clay.clone().lerp(tint, 1));
  const belly = coat(cream.clone().lerp(tint, .27));
  const pads = coat(clay.clone().lerp(tint, .82));
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
  model.traverse(mesh => {
    if (mesh.geometry === ball && mesh.scale.x > .17 && [skin, pads, belly].includes(mesh.material)) {
      mesh.castShadow = true;
    }
  });
  if (hat) addFedora(upper);
  const shadow = new THREE.Mesh(shadowPlane, new THREE.MeshBasicMaterial({ map: shadowMap, transparent: true, depthWrite: false }));
  shadow.rotation.set(-Math.PI / 2, 0, 0, 'YXZ');
  return { root, model, upper, eyes, shadow };
}

export function setEyes(eyes, openness) {
  for (const eye of eyes) {
    eye.open.visible = openness > .12;
    eye.open.scale.y = openness;
    eye.closed.visible = openness <= .24;
  }
}
