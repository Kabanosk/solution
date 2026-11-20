"use client";

import { useEffect, useRef, useState, type PointerEvent } from "react";
import { usePlotArea } from "recharts";
import { panViewport, type PlotViewport } from "@/lib/plot-viewport";

export function PlotPanSurface({ viewport, onPan, onZoom, onReset, onFocus }: {
  viewport: PlotViewport;
  onPan: (view: PlotViewport) => void;
  onZoom: (factor: number, anchor: { x: number; y: number }) => void;
  onReset: () => void;
  onFocus: () => void;
}) {
  const area = usePlotArea();
  const surface = useRef<SVGRectElement>(null);
  const origin = useRef<{ id: number; x: number; y: number; width: number; height: number; view: PlotViewport } | null>(null);
  const [dragging, setDragging] = useState(false);
  const hasArea = Boolean(area);
  useEffect(() => {
    const element = surface.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey || event.altKey || event.metaKey || !event.deltaY || origin.current) return;
      const box = element.getBoundingClientRect();
      if (!box.width || !box.height) return;
      // React delegates wheel events passively. A native non-passive listener
      // keeps Ctrl+wheel from also zooming the browser over this plot.
      event.preventDefault();
      event.stopPropagation();
      const pixels = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? box.height : 1);
      const factor = Math.exp(Math.max(-400, Math.min(400, pixels)) * 0.002);
      onZoom(factor, { x: (event.clientX - box.left) / box.width, y: 1 - (event.clientY - box.top) / box.height });
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [hasArea, onZoom]);
  if (!area) return null;

  function finish(event: PointerEvent<SVGRectElement>) {
    if (origin.current?.id !== event.pointerId) return;
    origin.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  return <rect id="scout-plot-pan-surface" ref={surface} x={area.x} y={area.y} width={area.width} height={area.height}
    fill="transparent" aria-hidden="true" className={dragging ? "touch-none cursor-grabbing" : "touch-none cursor-grab"}
    onPointerDown={event => {
      if (!event.isPrimary || event.button !== 0) return;
      event.preventDefault();
      onFocus();
      const box = event.currentTarget.getBoundingClientRect();
      origin.current = { id: event.pointerId, x: event.clientX, y: event.clientY, width: box.width, height: box.height, view: viewport };
      event.currentTarget.setPointerCapture(event.pointerId);
      setDragging(true);
    }}
    onPointerMove={event => {
      const start = origin.current;
      if (!start || start.id !== event.pointerId || !start.width || !start.height) return;
      onPan(panViewport(start.view, (event.clientX - start.x) / start.width, (event.clientY - start.y) / start.height));
    }}
    onPointerUp={finish} onPointerCancel={finish} onLostPointerCapture={finish}
    onDoubleClick={onReset} />;
}
