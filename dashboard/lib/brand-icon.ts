// The deck's hat mark (presentation/index.html #mark), verbatim.
export const MARK_PATH = "M16 43l3-19c1-6 5-9 9-9 5-1 9 3 13 2 5-1 6-6 12-6 4 0 6 3 7 6l8 18 7-2c3-1 5 2 3 5-7 11-23 19-43 21-11 1-23 0-30-5-3-2-3-5 0-7l11-4Zm9 2c11 5 28 1 39-7-5 9-28 15-39 7Z";
export const MARK_TRANSFORM = "translate(1.5 2.7) scale(.26)";
export const MARK_BACKGROUND = "#141416";

/** White mark on a #141416 rounded square (radius 22%), as a standalone SVG document. */
export function scoutIconSvg(size = 512): string {
  const radius = Math.round(size * 0.22 * 100) / 100;
  // The glyph fills ~78% of the square, centred.
  const inner = size * 0.78;
  const offset = (size - inner) / 2;
  const scale = inner / 24;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">` +
    `<rect width="${size}" height="${size}" rx="${radius}" fill="${MARK_BACKGROUND}"/>` +
    `<g transform="translate(${offset} ${offset}) scale(${scale})"><path fill="#ffffff" fill-rule="evenodd" transform="${MARK_TRANSFORM}" d="${MARK_PATH}"/></g>` +
    `</svg>\n`;
}
