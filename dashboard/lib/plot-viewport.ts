export type PlotViewport = { x: [number, number]; y: [number, number] };

export function forecastViewport(points: { day: number; balance: number }[], buffer: number): PlotViewport {
  const values = [buffer, ...points.map(point => point.balance)];
  const low = Math.min(...values);
  const high = Math.max(...values);
  const padding = Math.max(100, (high - low) * 0.08);
  return { x: [0, Math.max(1, ...points.map(point => point.day))], y: [low - padding, high + padding] };
}

export function zoomViewport(view: PlotViewport, factor: number, anchor = { x: 0.5, y: 0.5 }): PlotViewport {
  const zoom = ([low, high]: [number, number], min: number, max: number, fraction: number): [number, number] => {
    const position = Math.max(0, Math.min(1, fraction));
    const center = low + (high - low) * position;
    const span = Math.max(min, Math.min(max, (high - low) * factor));
    return [center - span * position, center + span * (1 - position)];
  };
  return { x: zoom(view.x, 1 / 24, 365000, anchor.x), y: zoom(view.y, 1, 1e12, anchor.y) };
}

// Fractions represent movement of the plotted content, not of the camera.
export function panViewport(view: PlotViewport, dx: number, dy: number): PlotViewport {
  const x = -dx * (view.x[1] - view.x[0]);
  const y = dy * (view.y[1] - view.y[0]);
  return { x: [view.x[0] + x, view.x[1] + x], y: [view.y[0] + y, view.y[1] + y] };
}
