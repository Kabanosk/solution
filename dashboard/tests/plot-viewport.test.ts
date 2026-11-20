import { test } from "node:test";
import assert from "node:assert/strict";
import { forecastViewport, panViewport, zoomViewport } from "../lib/plot-viewport.ts";

test("fit includes the forecast and safety buffer, even for flat or short series", () => {
  const fit = forecastViewport([{ day: 0, balance: 30000 }, { day: 90, balance: 40000 }], 18000);
  assert.deepEqual(fit.x, [0, 90]);
  assert.ok(fit.y[0] < 18000 && fit.y[1] > 40000);
  const flat = forecastViewport([{ day: 0, balance: 0 }], 0);
  assert.ok(flat.x[1] > flat.x[0] && flat.y[1] > flat.y[0]);
});

test("zoom preserves the center on both axes and zoom-out reverses zoom-in", () => {
  const initial = { x: [0, 90] as [number, number], y: [10000, 40000] as [number, number] };
  const zoomed = zoomViewport(initial, 0.8);
  assert.deepEqual(zoomed, { x: [9, 81], y: [13000, 37000] });
  assert.deepEqual(zoomViewport(zoomed, 1.25), initial);
  let extreme = initial;
  for (let i = 0; i < 1000; i++) extreme = zoomViewport(extreme, 0.8);
  assert.ok(extreme.x[1] > extreme.x[0] && extreme.y[1] > extreme.y[0]);
});

test("panning moves both axes with the pointer while preserving their scales", () => {
  const initial = { x: [0, 90] as [number, number], y: [10000, 40000] as [number, number] };
  const moved = panViewport(initial, 0.25, 0.1);
  assert.deepEqual(moved, { x: [-22.5, 67.5], y: [13000, 43000] });
  assert.deepEqual(panViewport(moved, -0.25, -0.1), initial);
  assert.deepEqual(initial.x, [0, 90]);
});

test("pointer-anchored zoom preserves the data coordinate under the pointer", () => {
  const initial = { x: [0, 100] as [number, number], y: [1000, 5000] as [number, number] };
  const anchor = { x: 0.25, y: 0.75 };
  const zoomed = zoomViewport(initial, 0.5, anchor);
  assert.deepEqual(zoomed, { x: [12.5, 62.5], y: [2500, 4500] });
  assert.equal(zoomed.x[0] + (zoomed.x[1] - zoomed.x[0]) * anchor.x, 25);
  assert.equal(zoomed.y[0] + (zoomed.y[1] - zoomed.y[0]) * anchor.y, 4000);
  assert.deepEqual(zoomViewport(zoomed, 2, anchor), initial);
});
